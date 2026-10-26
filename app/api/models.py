"""Strict request models for the nextWireless M1 contract v1.3 proposal."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrganizationIn(ContractModel):
    org_code: str
    name: str
    version: int = Field(ge=1)
    enabled: bool = True


class SiteIn(ContractModel):
    org_code: str
    site_code: str
    name: str
    version: int = Field(ge=1)
    timezone: str = "UTC"


class ZoneIn(ContractModel):
    org_code: str
    site_code: str
    zone_code: str
    name: str
    version: int = Field(ge=1)


class DataPointIn(ContractModel):
    code: str
    label: str
    data_type: Literal["number", "string", "boolean"]
    channel: Literal["telemetry", "status"] = "telemetry"
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    allowed_values: list[Any] | None = None
    precision: int | None = Field(default=None, ge=0, le=12)
    required: bool = False


class EventTypeIn(ContractModel):
    code: str
    label: str


class ProfileIn(ContractModel):
    profile_code: str
    version: int = Field(ge=1)
    signal_model: Literal["TELEMETRY", "EVENT"]
    protocol: Literal["MQTT"]
    expected_interval_s: int = Field(gt=0)
    heartbeat_interval_s: int = Field(gt=0)
    offline_after_missed: int = Field(gt=0)
    data_points: list[DataPointIn] = Field(default_factory=list)
    event_types: list[EventTypeIn] = Field(default_factory=list)


class DeviceIn(ContractModel):
    device_id: str
    org_code: str
    site_code: str
    zone_code: str
    profile_code: str
    status: Literal["registered", "active", "maintenance", "retired"]
    connectivity_type: Literal["WIFI", "LAN", "4G", "5G"]
    config_version: int = Field(ge=1)
    enabled: bool = True
    credential_ref: str | None = None


class RuleScope(ContractModel):
    type: Literal["DEVICE", "PROFILE", "ZONE", "SITE", "ORGANIZATION"]
    ref: str


class RuleSchedule(ContractModel):
    timezone: str
    weekdays: list[int]
    business_start: str
    business_end: str

    @model_validator(mode="after")
    def valid_weekdays(self):
        if not self.weekdays or any(day < 1 or day > 7 for day in self.weekdays):
            raise ValueError("weekdays must use ISO values 1..7")
        return self


class RuleIn(ContractModel):
    rule_id: str
    version: int = Field(ge=1)
    org_code: str
    rule_type: Literal["THRESHOLD", "STATE_CHANGE"]
    scope: RuleScope
    data_point: str | None = None
    operator: Literal[">", ">=", "<", "<=", "==", "!="] | None = None
    threshold: float | None = None
    duration_s: int = Field(default=0, ge=0)
    hysteresis: float = Field(default=0, ge=0)
    clear_duration_s: int = Field(default=0, ge=0)
    quiet_window_s: int = Field(default=0, ge=0)
    event_type: str | None = None
    open_limit_s: int | None = Field(default=None, ge=0)
    schedule: RuleSchedule | None = None
    outside_schedule_immediate: bool = False
    severity: Literal["Low", "Medium", "High", "Critical"] = "High"
    auto_close: bool = False
    active: bool = True

    @model_validator(mode="after")
    def valid_rule_shape(self):
        if self.rule_type == "THRESHOLD" and (self.data_point is None or self.threshold is None):
            raise ValueError("THRESHOLD requires data_point and threshold")
        if self.rule_type == "STATE_CHANGE" and (self.event_type is None or self.open_limit_s is None):
            raise ValueError("STATE_CHANGE requires event_type and open_limit_s")
        return self


class AlarmStateIn(ContractModel):
    alarm_key: str
    org_code: str
    state: Literal["acknowledged", "in_progress", "closed"]
    changed_at: datetime
    changed_by: str
    version: int = Field(ge=1)


class BridgeEventIn(ContractModel):
    event_id: str
    event_type: str
    ts: datetime
    data: dict[str, Any]


class BridgeAlarmIn(ContractModel):
    event_id: str
    org_code: str
    alarm_key: str
    episode_key: str
    kind: Literal["occurrence", "cleared"]
    rule_id: str
    rule_version: int = Field(ge=1)
    device_id: str
    zone_code: str
    severity: Literal["Low", "Medium", "High", "Critical"]
    episode_start: datetime
    occurrence_seq: int = Field(ge=1)
    raised_at: datetime | None = None
    cleared_at: datetime | None = None
    event: BridgeEventIn

    @model_validator(mode="after")
    def valid_alarm_shape(self):
        if self.alarm_key != self.episode_key:
            raise ValueError("alarm_key and episode_key must identify the same episode")
        if self.kind == "occurrence" and self.raised_at is None:
            raise ValueError("occurrence requires raised_at")
        if self.kind == "cleared" and self.cleared_at is None:
            raise ValueError("cleared requires cleared_at")
        return self


class BridgeStatusIn(ContractModel):
    event_id: str
    org_code: str
    device_id: str
    state: Literal["online", "offline"]
    since: datetime
    last_seen: datetime | None = None
    reason: Literal["telemetry", "lwt", "timeout"]
    fw_version: str | None = None
