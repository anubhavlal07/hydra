from __future__ import annotations

from dataclasses import dataclass

REWRITE = "rewrite"
DESIGNATED = "designated"
LOCAL_PTR = "local ptr"
LOCAL_ZONE = "local zone"
PAUSED = "paused"
DEVICE_RULE = "device rule"
SCHEDULE = "schedule"
ALLOWED = "allowed"
APP = "app"
BLOCKLIST = "blocklist"
SAFE_SEARCH = "safe search"

LAYERS = (
    REWRITE,
    DESIGNATED,
    LOCAL_PTR,
    LOCAL_ZONE,
    PAUSED,
    DEVICE_RULE,
    SCHEDULE,
    ALLOWED,
    APP,
    BLOCKLIST,
    SAFE_SEARCH,
)


@dataclass(frozen=True, slots=True)
class Decision:
    layer: str
    label: str
    blocked: bool = False
    source: str | None = None
    detail: object = None
    rule: str | None = None
    expires_at: float | None = None

    def as_dict(self) -> dict:
        shown = {
            "layer": self.layer,
            "label": self.label,
            "blocked": self.blocked,
            "source": self.source,
            "rule": self.rule,
        }
        if self.expires_at is not None:
            shown["expires_at"] = self.expires_at
        return shown


PAUSED_DECISION = Decision(layer=PAUSED, label=PAUSED)
DESIGNATED_DECISION = Decision(layer=DESIGNATED, label=DESIGNATED)
DEVICE_RULE_DECISION = Decision(layer=DEVICE_RULE, label=DEVICE_RULE, blocked=True)
DEVICE_ALLOW_DECISION = Decision(layer=ALLOWED, label="allowed by @@ device")
