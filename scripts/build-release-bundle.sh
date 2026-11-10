#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_IMAGE="${APP_IMAGE:?Set APP_IMAGE to the tested local image tag/digest}"
RELEASE_VERSION="${RELEASE_VERSION:-$(date -u +%Y%m%dT%H%M%SZ)}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT_DIR/releases}"
safe_version="${RELEASE_VERSION//[^A-Za-z0-9._-]/-}"
safe_platform="${TARGET_PLATFORM//\//-}"
bundle_name="nextwireless-dev2-${safe_version}-${safe_platform}"
bundle="$OUTPUT_ROOT/$bundle_name"
archive="$OUTPUT_ROOT/$bundle_name.tar.gz"

command -v docker >/dev/null 2>&1 || { echo "ERROR: docker is required" >&2; exit 1; }
command -v sha256sum >/dev/null 2>&1 || { echo "ERROR: sha256sum is required" >&2; exit 1; }
docker image inspect "$APP_IMAGE" >/dev/null 2>&1 || { echo "ERROR: APP_IMAGE is not present locally" >&2; exit 1; }

image_os="$(docker image inspect -f '{{.Os}}' "$APP_IMAGE")"
image_arch="$(docker image inspect -f '{{.Architecture}}' "$APP_IMAGE")"
actual_platform="$image_os/$image_arch"
[[ "$actual_platform" == "$TARGET_PLATFORM" ]] || {
  echo "ERROR: image platform $actual_platform does not match TARGET_PLATFORM=$TARGET_PLATFORM" >&2
  exit 1
}

[[ ! -e "$bundle" && ! -e "$archive" ]] || {
  echo "ERROR: release output already exists: $bundle_name" >&2
  exit 1
}

mkdir -p "$bundle/images" "$bundle/db" "$bundle/mosquitto" "$bundle/deploy/nginx" \
  "$bundle/docs/contracts" "$bundle/scripts"
cp compose.yaml compose.deploy.yaml deploy.sh README-DEPLOY.md .dockerignore "$bundle/"
cp -R app tests "$bundle/"
cp -R db/migrations db/rollback "$bundle/db/"
cp -R docs/contracts/. "$bundle/docs/contracts/"
cp mosquitto/mosquitto.conf mosquitto/acl mosquitto/start-secure.sh "$bundle/mosquitto/"
cp deploy/nginx/data-api-tls.conf "$bundle/deploy/nginx/"

image_archive="$bundle/images/nextwireless-dev2-app.tar"
docker save --output "$image_archive" "$APP_IMAGE"
image_sha="$(sha256sum "$image_archive" | awk '{print $1}')"

awk -v image="$APP_IMAGE" -v image_sha="$image_sha" '
  /^APP_IMAGE=/ {print "APP_IMAGE=" image; next}
  /^APP_IMAGE_ARCHIVE=/ {print "APP_IMAGE_ARCHIVE=images/nextwireless-dev2-app.tar"; next}
  /^APP_IMAGE_ARCHIVE_SHA256=/ {print "APP_IMAGE_ARCHIVE_SHA256=" image_sha; next}
  {print}
' deploy.env.example > "$bundle/.env.example"

cat > "$bundle/RELEASE-METADATA.txt" <<EOF
release_version=$RELEASE_VERSION
application_image=$APP_IMAGE
platform=$actual_platform
created_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

(cd "$bundle" && sha256sum images/nextwireless-dev2-app.tar compose.yaml \
  compose.deploy.yaml deploy.sh .env.example README-DEPLOY.md > SHA256SUMS)

if find "$bundle" -type f \( -name '.env' -o -name '*.key' -o -name '*.dump' \
    -o -name '*.backup' -o -name 'nvs*.bin' \) | grep -q .; then
  echo "ERROR: prohibited secret/data file found in release bundle" >&2
  exit 1
fi
if grep -RIl --exclude='*.py' --exclude='*.json' --exclude='*.md' \
    'BEGIN .*PRIVATE KEY' "$bundle" | grep -q .; then
  echo "ERROR: private key material found in release bundle" >&2
  exit 1
fi

tar -czf "$archive" -C "$OUTPUT_ROOT" "$bundle_name"
archive_sha="$(sha256sum "$archive" | awk '{print $1}')"
printf 'BUNDLE_DIR=%s\nBUNDLE_ARCHIVE=%s\nBUNDLE_SHA256=%s\nPLATFORM=%s\n' \
  "$bundle" "$archive" "$archive_sha" "$actual_platform"
