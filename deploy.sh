#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env}"
PROJECT="${COMPOSE_PROJECT_NAME:-$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$ENV_FILE" 2>/dev/null | tail -n 1)}"
PROJECT="${PROJECT:-nextwireless-dev2}"
COMPOSE=(docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" -f compose.yaml -f compose.deploy.yaml)
CORE=(postgres mqtt data-plane data-api)
SERVICES=("${CORE[@]}")

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '==> %s\n' "$*"; }
require_file() { [[ -s "$1" ]] || die "Missing required file: $1"; }
env_value() {
  local name="$1" fallback="${2:-}" value
  value="${!name:-$(sed -n "s/^${name}=//p" "$ENV_FILE" | tail -n 1)}"
  printf '%s' "${value:-$fallback}"
}

command -v docker >/dev/null 2>&1 || die "Docker is not installed or not in PATH"
docker info >/dev/null 2>&1 || die "Docker daemon is unavailable to this user"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
require_file "$ENV_FILE"
require_file compose.yaml
require_file compose.deploy.yaml
require_file db/migrations/019_rule_version_episode_freeze.sql
require_file mosquitto/certs/ca.crt
require_file mosquitto/certs/server.crt
require_file mosquitto/certs/server.key
require_file .secrets/mqtt-broker.env
require_file .secrets/mqtt-data-plane.env

for variable in APP_IMAGE PG_PASSWORD DP_INBOUND_TOKEN MQTT_BIND_IP; do
  value="$(env_value "$variable")"
  [[ -n "$value" ]] || die "$variable is missing from $ENV_FILE"
  [[ "$value" != CHANGE_ME* ]] || die "$variable still contains a CHANGE_ME placeholder"
done

MODE="$(env_value DEPLOY_MODE standalone)"
case "$MODE" in
  standalone) ;;
  integrated)
    for variable in DEV1_BASE_URL DEV1_API_KEY DEV1_API_SECRET FRAPPE_SITE_NAME \
      REALTIME_EVENT_HIGH_WATER_ID REALTIME_STATUS_HIGH_WATER_ID REALTIME_ARMED_AT \
      REALTIME_ORG REALTIME_DEVICES BRIDGE_ALARM_URL BRIDGE_STATUS_URL \
      IF05_E2E_HIGH_WATER_ID IF05_E2E_ARMED_AT IF05_E2E_ORG IF05_E2E_DEVICES IF05_E2E_RULES; do
      [[ -n "$(env_value "$variable")" ]] || die "$variable is required for DEPLOY_MODE=integrated"
    done
    [[ "$(env_value REALTIME_E2E_ARMED false)" == true ]] || die "REALTIME_E2E_ARMED=true is required"
    [[ "$(env_value OUTBOX_DELIVERY_ENABLED false)" == true ]] || die "OUTBOX_DELIVERY_ENABLED=true is required"
    [[ "$(env_value IF05_E2E_ARMED false)" == true ]] || die "IF05_E2E_ARMED=true is required"
    [[ "$(env_value BRIDGE_MODE mock)" == real ]] || die "BRIDGE_MODE=real is required"
    [[ "$(env_value BRIDGE_ALARM_PROTOCOL mock)" == frappe ]] || die "BRIDGE_ALARM_PROTOCOL=frappe is required"
    COMPOSE+=(--profile realtime --profile outbound)
    SERVICES+=(realtime-worker outbox-worker)
    if [[ "$(env_value ENABLE_SNAPSHOT_RECONCILIATION false)" == true ]]; then
      [[ -n "$(env_value DEV1_CONFIG_URL)" && -n "$(env_value DEV1_CONFIG_DEVICE_IDS)" && -n "$(env_value DEV1_SITE_NAME)" ]] || \
        die "DEV1_CONFIG_URL, DEV1_CONFIG_DEVICE_IDS and DEV1_SITE_NAME are required for snapshot reconciliation"
      COMPOSE+=(--profile integration)
      SERVICES+=(snapshot-worker)
    fi
    ;;
  *) die "DEPLOY_MODE must be standalone or integrated" ;;
esac

# A secret-bearing .env must not be readable or writable by group/others.
if [[ "$(uname -s)" == "Linux" ]]; then
  env_mode="$(stat -c '%a' "$ENV_FILE")"
  [[ "$env_mode" =~ ^[0-7]{3,4}$ ]] || die "Cannot read permissions for $ENV_FILE"
  if (( (8#$env_mode & 8#077) != 0 )); then
    die "$ENV_FILE has unsafe permissions ($env_mode); run: chmod 600 $ENV_FILE"
  fi
  available_kib="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
else
  # Test-only portability for Git Bash/CI. Production Linux never uses this.
  available_kib="${DEPLOY_TEST_AVAILABLE_MEMORY_KIB:-}"
fi
[[ "$available_kib" =~ ^[0-9]+$ ]] || die "Cannot determine available memory"
minimum_kib=1572864
[[ "$MODE" == integrated ]] && minimum_kib=1933312
if (( available_kib < minimum_kib )); then
  die "Insufficient available RAM for DEPLOY_MODE=$MODE"
fi

info "Validating merged Docker Compose configuration"
"${COMPOSE[@]}" config --quiet

image_archive="$(env_value APP_IMAGE_ARCHIVE)"
if [[ -n "$image_archive" ]]; then
  require_file "$image_archive"
  expected_sha="$(env_value APP_IMAGE_ARCHIVE_SHA256)"
  [[ "$expected_sha" =~ ^[A-Fa-f0-9]{64}$ ]] || die "APP_IMAGE_ARCHIVE_SHA256 must be a SHA-256 hex digest"
  checksum_line="$(sha256sum "$image_archive")"
  checksum_line="${checksum_line#\\}" # GNU escaping marker for paths containing backslashes.
  actual_sha="${checksum_line%% *}"
  [[ "${actual_sha,,}" == "${expected_sha,,}" ]] || die "Application image archive checksum mismatch"
  info "Loading the verified application image archive"
  docker load --input "$image_archive" >/dev/null
  docker image inspect "$(env_value APP_IMAGE)" >/dev/null 2>&1 || \
    die "Loaded archive does not provide APP_IMAGE=$(env_value APP_IMAGE)"
elif [[ "${DEPLOY_BUILD_LOCAL:-$(sed -n 's/^DEPLOY_BUILD_LOCAL=//p' "$ENV_FILE" | tail -n 1)}" == "true" ]]; then
  info "Building the approved application artifact locally"
  "${COMPOSE[@]}" build data-plane
else
  info "Pulling the approved application artifact"
  "${COMPOSE[@]}" pull data-plane
fi

info "Preparing the public MQTT CA volume (private keys are not copied)"
"${COMPOSE[@]}" --profile mqtt-ca-init run --rm mqtt-ca-init

# The official PostgreSQL entrypoint executes /docker-entrypoint-initdb.d
# only on a fresh data volume. Existing volumes must be migrated explicitly.
info "Starting PostgreSQL before application services"
"${COMPOSE[@]}" up -d --no-build postgres

# Check TCP readiness, not the local Unix socket: during first initialization
# the temporary PostgreSQL server can accept socket connections before the
# initdb SQL scripts have completed.
postgres_ready=false
for (( attempt=1; attempt<=60; attempt++ )); do
  if "${COMPOSE[@]}" exec -T postgres \
      pg_isready -h 127.0.0.1 -p 5432 -U nw_user -d nextwireless >/dev/null 2>&1; then
    postgres_ready=true
    break
  fi
  sleep 2
done
[[ "$postgres_ready" == true ]] || die "PostgreSQL did not become ready"

# 019 requires the earlier registry and episode tables. Refuse to proceed if
# the target database has not yet received the prerequisite schema.
prerequisites="$("${COMPOSE[@]}" exec -T postgres \
  psql -X -v ON_ERROR_STOP=1 -A -t -U nw_user -d nextwireless -c \
  "SELECT to_regclass('public.rule_runtime_state') IS NOT NULL
      AND to_regclass('public.alarm_episode_mappings') IS NOT NULL
      AND to_regclass('public.integration_identity_mappings') IS NOT NULL")"
[[ "$prerequisites" == t ]] || die "Required pre-019 schema is missing; stop before starting services"

# 019 uses IF NOT EXISTS / ON CONFLICT guards. Run it in a transaction so a
# failure does not leave a partially applied migration.
info "Applying migration 019 (Rule version / episode freeze)"
"${COMPOSE[@]}" exec -T postgres \
  psql -X -v ON_ERROR_STOP=1 --single-transaction \
  -U nw_user -d nextwireless \
  -f /docker-entrypoint-initdb.d/019_rule_version_episode_freeze.sql || \
  die "Migration 019 failed; application services were not started"

info "Starting services for project $PROJECT in $MODE mode"
"${COMPOSE[@]}" up -d --no-build "${SERVICES[@]}"

deadline=$((SECONDS + 180))
for service in "${SERVICES[@]}"; do
  while :; do
    cid="$("${COMPOSE[@]}" ps -q "$service")"
    [[ -n "$cid" ]] || die "$service has no container"
    state="$(docker inspect -f '{{.State.Status}}' "$cid")"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid")"
    [[ "$state" == running && ( "$health" == healthy || "$health" == none ) ]] && break
    [[ "$state" == exited || "$state" == dead ]] && die "$service stopped; inspect with: ${COMPOSE[*]} logs $service"
    (( SECONDS < deadline )) || die "Timed out waiting for $service (state=$state health=$health)"
    sleep 3
  done
done

# Workers have no HTTP endpoint. A short stabilization gate catches fail-closed
# configuration exits/restart loops before reporting deployment success.
sleep 5
for service in "${SERVICES[@]}"; do
  cid="$("${COMPOSE[@]}" ps -q "$service")"
  [[ -n "$cid" && "$(docker inspect -f '{{.State.Status}}' "$cid")" == running ]] || \
    die "$service did not remain stable after startup"
done

info "Deployment completed"
"${COMPOSE[@]}" ps
printf '\nHealth: http://%s:%s/api/v1/health\n' \
  "$(env_value DATA_API_BIND_IP 127.0.0.1)" \
  "$(env_value DATA_API_PORT 8002)"
