from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .localzones import DEFAULT_ZONES
from .localzones import parse as _parse_zones


class ConfigError(RuntimeError):
    pass


def _load_dotenv() -> None:
    path = Path(__file__).resolve().parents[2] / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AccountConfig:
    label: str
    username: str
    password: str
    device_id: str = ""
    doh_url: str = ""
    enabled: bool = True

    @property
    def redacted(self) -> str:
        user, _, domain = self.username.partition("@")
        keep = user[:2] if len(user) > 2 else user
        return f"{keep}***@{domain}" if domain else f"{keep}***"


@dataclass(frozen=True)
class DeviceConfig:
    id: str
    name: str

    @property
    def token(self) -> str:
        return device_token(self.id)


TOKEN_LENGTH = 16


def device_token(device_id: str) -> str:
    digest = hmac.new(
        settings.resolver_secret.encode(), f"device:{device_id}".encode(), hashlib.sha256
    ).digest()
    return base64.b32encode(digest).decode().lower().rstrip("=")[:TOKEN_LENGTH]


def _parse_accounts(raw: str) -> list[AccountConfig]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "HYDRA_ADGUARD_ACCOUNTS must be a JSON array of account objects with "
            "label, username and password keys. JSON error: " + str(exc)
        ) from exc

    if not isinstance(parsed, list):
        raise ConfigError("HYDRA_ADGUARD_ACCOUNTS must be a JSON *array* of account objects")

    accounts: list[AccountConfig] = []
    seen: set[str] = set()
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ConfigError(f"HYDRA_ADGUARD_ACCOUNTS[{index}] is not an object")
        username = str(item.get("username", "")).strip()
        password = str(item.get("password", ""))
        if not username or not password:
            raise ConfigError(
                f"HYDRA_ADGUARD_ACCOUNTS[{index}] needs both a username and a password"
            )
        label = str(item.get("label", "")).strip() or username.partition("@")[0]
        if label in seen:
            raise ConfigError(f"duplicate account label {label!r} -- labels key the dashboard")
        seen.add(label)
        accounts.append(
            AccountConfig(
                label=label,
                username=username,
                password=password,
                device_id=str(item.get("device_id", "")).strip(),
                doh_url=str(item.get("doh_url", "")).strip().rstrip("/"),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return accounts


def _parse_devices(raw: str) -> list[DeviceConfig]:
    devices: list[DeviceConfig] = []
    seen: set[str] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        device_id, _, name = chunk.partition(":")
        device_id = device_id.strip()
        if not device_id.replace("-", "").replace("_", "").isalnum():
            raise ConfigError(
                f"device id {device_id!r} must be alphanumeric (dashes and underscores allowed)"
            )
        if device_id in seen:
            raise ConfigError(f"duplicate device id {device_id!r}")
        seen.add(device_id)
        devices.append(DeviceConfig(id=device_id, name=name.strip() or device_id))
    return devices


def _normalise_address(raw: str) -> str:
    value = raw.strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)


def _parse_rewrites(raw: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for pair in raw.split(","):
        name, _, address = pair.partition("=")
        name, address = name.strip().strip(".").lower(), address.strip()
        if not name or not address:
            continue
        try:
            ipaddress.ip_address(address)
        except ValueError:
            continue
        found[name] = address
    return found


DIGEST_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _digest_day(raw: str) -> int:
    wanted = raw.strip().lower()[:3]
    return DIGEST_DAYS.index(wanted) if wanted in DIGEST_DAYS else 0


def _hostname() -> str:
    import socket

    try:
        return socket.gethostname()[:60] or "hydra"
    except OSError:
        return "hydra"


def _api_host() -> str:
    return urlsplit(_env("HYDRA_PUBLIC_API_BASE", "http://localhost:8080")).hostname or ""


@dataclass(frozen=True)
class Settings:
    service_name: str = field(default_factory=lambda: _env("HYDRA_SERVICE_NAME", "Hydra DNS"))
    public_api_base: str = field(
        default_factory=lambda: _env("HYDRA_PUBLIC_API_BASE", "http://localhost:8080").rstrip("/")
    )

    accounts: list[AccountConfig] = field(
        default_factory=lambda: _parse_accounts(_env("HYDRA_ADGUARD_ACCOUNTS"))
    )

    seed_devices: list[DeviceConfig] = field(
        default_factory=lambda: _parse_devices(_env("HYDRA_DEVICES", "default"))
    )
    resolver_secret: str = field(default_factory=lambda: _env("HYDRA_RESOLVER_SECRET"))
    fallback_doh_url: str = field(
        default_factory=lambda: _env("HYDRA_FALLBACK_DOH", "https://dns.quad9.net/dns-query")
    )
    allow_anonymous: bool = field(default_factory=lambda: _env_bool("HYDRA_ALLOW_ANONYMOUS", False))
    strategy: str = field(default_factory=lambda: _env("HYDRA_STRATEGY", "balanced"))
    balance_floor: float = field(default_factory=lambda: _env_float("HYDRA_BALANCE_FLOOR", 0.01))
    upstream_timeout_s: float = field(
        default_factory=lambda: _env_int("HYDRA_UPSTREAM_TIMEOUT_MS", 4000) / 1000
    )
    cache_max_entries: int = field(
        default_factory=lambda: _env_int("HYDRA_CACHE_MAX_ENTRIES", 20_000)
    )
    cache_min_ttl_s: int = field(default_factory=lambda: _env_int("HYDRA_CACHE_MIN_TTL", 30))
    cache_max_ttl_s: int = field(default_factory=lambda: _env_int("HYDRA_CACHE_MAX_TTL", 86_400))
    cache_blocked_min_ttl_s: int = field(
        default_factory=lambda: _env_int("HYDRA_CACHE_BLOCKED_MIN_TTL", 1800)
    )
    cache_stale_window_s: int = field(
        default_factory=lambda: _env_int("HYDRA_CACHE_STALE_WINDOW", 86_400)
    )
    cache_file: str = field(default_factory=lambda: _env("HYDRA_CACHE_FILE"))
    cache_save_every_s: int = field(default_factory=lambda: _env_int("HYDRA_CACHE_SAVE_EVERY", 300))
    cache_save_max: int = field(default_factory=lambda: _env_int("HYDRA_CACHE_SAVE_MAX", 10_000))
    cache_warm_count: int = field(default_factory=lambda: _env_int("HYDRA_CACHE_WARM", 50))
    rate_limit_qps: int = field(default_factory=lambda: _env_int("HYDRA_RATE_LIMIT_QPS", 80))
    device_hourly_budget: int = field(
        default_factory=lambda: _env_int("HYDRA_DEVICE_HOURLY_BUDGET", 0)
    )
    timezone: str = field(default_factory=lambda: _env("HYDRA_TIMEZONE", "UTC"))

    dashboard_password: str = field(default_factory=lambda: _env("HYDRA_DASHBOARD_PASSWORD"))
    session_secret: str = field(default_factory=lambda: _env("HYDRA_SESSION_SECRET"))
    cookie_domain: str = field(default_factory=lambda: _env("HYDRA_COOKIE_DOMAIN"))
    session_ttl_s: int = field(
        default_factory=lambda: _env_int("HYDRA_SESSION_TTL", 60 * 60 * 24 * 14)
    )
    cors_origins: list[str] = field(
        default_factory=lambda: [
            o.strip().rstrip("/")
            for o in _env("HYDRA_CORS_ORIGINS", "http://localhost:3200").split(",")
            if o.strip()
        ]
    )

    rewrites: dict[str, str] = field(
        default_factory=lambda: _parse_rewrites(_env("HYDRA_REWRITES"))
    )
    block_sentinels: set[str] = field(
        default_factory=lambda: {
            _normalise_address(a)
            for a in _env("HYDRA_BLOCK_SENTINELS", "0.0.0.0,::,94.140.14.35,94.140.14.36").split(
                ","
            )
            if a.strip()
        }
    )
    block_soa_names: set[str] = field(
        default_factory=lambda: {
            name.strip().rstrip(".").lower()
            for name in _env("HYDRA_BLOCK_SOA", "fake-for-negative-caching.adguard.com").split(",")
            if name.strip()
        }
    )
    rewrite_ttl_s: int = field(default_factory=lambda: _env_int("HYDRA_REWRITE_TTL", 300))
    local_zones: tuple[str, ...] = field(
        default_factory=lambda: _parse_zones(_env("HYDRA_LOCAL_ZONES", ",".join(DEFAULT_ZONES)))
    )
    local_zone_ttl_s: int = field(default_factory=lambda: _env_int("HYDRA_LOCAL_ZONE_TTL", 3600))
    safe_search_ttl_s: int = field(default_factory=lambda: _env_int("HYDRA_SAFE_SEARCH_TTL", 300))

    prefetch: bool = field(default_factory=lambda: _env_bool("HYDRA_PREFETCH", True))
    prefetch_every_s: int = field(default_factory=lambda: _env_int("HYDRA_PREFETCH_EVERY", 30))
    prefetch_window_s: int = field(default_factory=lambda: _env_int("HYDRA_PREFETCH_WINDOW", 90))
    prefetch_min_hits: int = field(default_factory=lambda: _env_int("HYDRA_PREFETCH_MIN_HITS", 4))
    prefetch_max_per_run: int = field(default_factory=lambda: _env_int("HYDRA_PREFETCH_MAX", 20))

    cert_warn_days: int = field(default_factory=lambda: _env_int("HYDRA_CERT_WARN_DAYS", 14))
    auth_warn_failures: int = field(default_factory=lambda: _env_int("HYDRA_AUTH_WARN_FAILURES", 3))

    log_hidden_domains: list[str] = field(
        default_factory=lambda: [
            d.strip().strip(".").lower()
            for d in _env("HYDRA_LOG_HIDDEN_DOMAINS", _api_host()).split(",")
            if d.strip().strip(".")
        ]
    )

    log_level: str = field(default_factory=lambda: _env("HYDRA_LOG_LEVEL", "INFO"))
    http_host: str = field(default_factory=lambda: _env("HYDRA_HTTP_HOST", "0.0.0.0"))
    http_port: int = field(default_factory=lambda: _env_int("HYDRA_HTTP_PORT", 8080))
    http_keep_alive_s: int = field(default_factory=lambda: _env_int("HYDRA_HTTP_KEEP_ALIVE", 75))
    http_cert_file: str = field(
        default_factory=lambda: _env("HYDRA_HTTP_CERT") or _env("HYDRA_DOT_CERT")
    )
    http_key_file: str = field(
        default_factory=lambda: _env("HYDRA_HTTP_KEY") or _env("HYDRA_DOT_KEY")
    )

    dot_enabled: bool = field(default_factory=lambda: _env_bool("HYDRA_DOT_ENABLED", False))
    dot_host: str = field(default_factory=lambda: _env("HYDRA_DOT_HOST", "0.0.0.0"))
    dot_port: int = field(default_factory=lambda: _env_int("HYDRA_DOT_PORT", 853))
    dot_base_domain: str = field(
        default_factory=lambda: _env("HYDRA_DOT_BASE_DOMAIN").strip().strip(".").lower()
    )
    dot_cert_file: str = field(default_factory=lambda: _env("HYDRA_DOT_CERT"))
    dot_key_file: str = field(default_factory=lambda: _env("HYDRA_DOT_KEY"))
    dot_cert_poll_s: int = field(default_factory=lambda: _env_int("HYDRA_DOT_CERT_POLL", 300))

    doq_enabled: bool = field(default_factory=lambda: _env_bool("HYDRA_DOQ_ENABLED", False))
    doq_host: str = field(default_factory=lambda: _env("HYDRA_DOQ_HOST", "0.0.0.0"))
    doq_port: int = field(default_factory=lambda: _env_int("HYDRA_DOQ_PORT", 853))
    doq_idle_timeout_s: float = field(
        default_factory=lambda: _env_float("HYDRA_DOQ_IDLE_TIMEOUT", 30.0)
    )

    store_url: str = field(default_factory=lambda: _env("HYDRA_STORE_URL").rstrip("/"))
    store_key: str = field(default_factory=lambda: _env("HYDRA_STORE_KEY"))
    store_secret: str = field(default_factory=lambda: _env("HYDRA_STORE_SECRET"))
    store_refresh_s: int = field(default_factory=lambda: _env_int("HYDRA_STORE_REFRESH", 300))
    store_seed: bool = field(default_factory=lambda: _env_bool("HYDRA_STORE_SEED", False))
    device_cache_file: str = field(default_factory=lambda: _env("HYDRA_DEVICE_CACHE"))
    blocklist_dir: str = field(default_factory=lambda: _env("HYDRA_BLOCKLIST_DIR"))
    blocklist_refresh_s: int = field(
        default_factory=lambda: _env_int("HYDRA_BLOCKLIST_REFRESH", 86_400)
    )
    blocklist_max: int = field(default_factory=lambda: _env_int("HYDRA_BLOCKLIST_MAX", 8))
    blocklist_max_names: int = field(
        default_factory=lambda: _env_int("HYDRA_BLOCKLIST_MAX_NAMES", 400_000)
    )
    blocklist_max_bytes: int = field(
        default_factory=lambda: _env_int("HYDRA_BLOCKLIST_MAX_BYTES", 20 * 1024 * 1024)
    )
    app_dir: str = field(default_factory=lambda: _env("HYDRA_APP_DIR"))
    app_catalog_url: str = field(
        default_factory=lambda: _env(
            "HYDRA_APP_CATALOG_URL",
            "https://adguardteam.github.io/HostlistsRegistry/assets/services.json",
        )
    )
    app_catalog_refresh_s: int = field(
        default_factory=lambda: _env_int("HYDRA_APP_CATALOG_REFRESH", 86_400)
    )
    accounts_file: str = field(default_factory=lambda: _env("HYDRA_ACCOUNTS_FILE"))
    metrics_persist: bool = field(default_factory=lambda: _env_bool("HYDRA_METRICS_PERSIST", False))
    metrics_flush_s: int = field(default_factory=lambda: _env_int("HYDRA_METRICS_FLUSH", 60))

    instance_id: str = field(default_factory=lambda: _env("HYDRA_INSTANCE_ID") or _hostname())

    log_json: bool = field(default_factory=lambda: _env_bool("HYDRA_LOG_JSON", False))

    audit_persist: bool = field(default_factory=lambda: _env_bool("HYDRA_AUDIT_PERSIST", False))
    audit_flush_s: int = field(default_factory=lambda: _env_int("HYDRA_AUDIT_FLUSH", 120))
    audit_retention_days: int = field(
        default_factory=lambda: _env_int("HYDRA_AUDIT_RETENTION_DAYS", 365)
    )

    metrics_endpoint: bool = field(
        default_factory=lambda: _env_bool("HYDRA_METRICS_ENDPOINT", True)
    )
    metrics_cache_s: int = field(default_factory=lambda: _env_int("HYDRA_METRICS_CACHE", 30))
    metrics_top_devices: int = field(
        default_factory=lambda: _env_int("HYDRA_METRICS_TOP_DEVICES", 20)
    )

    alert_webhook: str = field(default_factory=lambda: _env("HYDRA_ALERT_WEBHOOK"))
    vapid_public_key: str = field(default_factory=lambda: _env("HYDRA_VAPID_PUBLIC_KEY"))
    vapid_private_key: str = field(default_factory=lambda: _env("HYDRA_VAPID_PRIVATE_KEY"))
    vapid_subject: str = field(
        default_factory=lambda: _env("HYDRA_VAPID_SUBJECT", "mailto:admin@example.com")
    )

    alert_every_s: int = field(default_factory=lambda: _env_int("HYDRA_ALERT_EVERY", 600))
    digest: bool = field(default_factory=lambda: _env_bool("HYDRA_DIGEST", False))
    digest_day: int = field(default_factory=lambda: _digest_day(_env("HYDRA_DIGEST_DAY", "Mon")))
    digest_hour: int = field(
        default_factory=lambda: max(0, min(23, _env_int("HYDRA_DIGEST_HOUR", 9)))
    )
    digest_devices: int = field(
        default_factory=lambda: max(1, min(50, _env_int("HYDRA_DIGEST_DEVICES", 10)))
    )
    first_seen: bool = field(default_factory=lambda: _env_bool("HYDRA_FIRST_SEEN", True))
    first_seen_per_device: int = field(
        default_factory=lambda: _env_int("HYDRA_FIRST_SEEN_MAX", 2000)
    )
    first_seen_warmup_s: int = field(
        default_factory=lambda: _env_int("HYDRA_FIRST_SEEN_WARMUP", 3600)
    )
    quiet_hours: float = field(default_factory=lambda: _env_float("HYDRA_QUIET_HOURS", 8.0))
    quiet_established_hours: int = field(
        default_factory=lambda: max(1, min(24, _env_int("HYDRA_QUIET_ESTABLISHED", 16)))
    )
    presence_file: str = field(default_factory=lambda: _env("HYDRA_PRESENCE_FILE"))
    suspicious_score: int = field(
        default_factory=lambda: max(1, min(100, _env_int("HYDRA_SUSPICIOUS_SCORE", 50)))
    )
    designated: bool = field(default_factory=lambda: _env_bool("HYDRA_DDR", True))
    rebind_guard: bool = field(default_factory=lambda: _env_bool("HYDRA_REBIND_GUARD", True))
    cname_uncloak: bool = field(default_factory=lambda: _env_bool("HYDRA_CNAME_UNCLOAK", True))
    rebind_allow: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            d.strip().strip(".").lower()
            for d in _env("HYDRA_REBIND_ALLOW", "plex.direct").split(",")
            if d.strip().strip(".")
        )
    )
    alert_repeat_s: int = field(default_factory=lambda: _env_int("HYDRA_ALERT_REPEAT", 21_600))
    alert_timeout_s: float = field(default_factory=lambda: _env_float("HYDRA_ALERT_TIMEOUT", 8.0))

    query_retention_days: int = field(
        default_factory=lambda: _env_int("HYDRA_QUERY_RETENTION_DAYS", 30)
    )
    export_max_rows: int = field(default_factory=lambda: _env_int("HYDRA_EXPORT_MAX_ROWS", 20_000))
    export_page_rows: int = field(default_factory=lambda: _env_int("HYDRA_EXPORT_PAGE_ROWS", 250))

    heavy_concurrency: int = field(default_factory=lambda: _env_int("HYDRA_HEAVY_CONCURRENCY", 1))
    bulk_max_devices: int = field(default_factory=lambda: _env_int("HYDRA_BULK_MAX_DEVICES", 50))

    policy_versions_kept: int = field(
        default_factory=lambda: _env_int("HYDRA_POLICY_VERSIONS_KEPT", 50)
    )
    policy_check_s: float = field(
        default_factory=lambda: max(0.0, _env_float("HYDRA_POLICY_CHECK_S", 900.0))
    )

    quota_warn_days: int = field(default_factory=lambda: _env_int("HYDRA_QUOTA_WARN_DAYS", 5))
    limits_refresh_s: int = field(default_factory=lambda: _env_int("HYDRA_LIMITS_REFRESH", 300))
    stats_cache_s: int = field(default_factory=lambda: _env_int("HYDRA_STATS_CACHE", 60))

    def hidden_from_logs(self, name: str) -> bool:
        name = name.strip().strip(".").lower()
        return any(
            name == domain or name.endswith(f".{domain}") for domain in self.log_hidden_domains
        )

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.resolver_secret:
            problems.append("HYDRA_RESOLVER_SECRET is unset -- device tokens cannot be derived")
        elif len(self.resolver_secret) < 16:
            problems.append("HYDRA_RESOLVER_SECRET is shorter than 16 characters")
        if not self.dashboard_password:
            problems.append("HYDRA_DASHBOARD_PASSWORD is unset -- the dashboard would be public")
        if not self.session_secret:
            problems.append("HYDRA_SESSION_SECRET is unset -- sessions cannot be signed")
        if self.strategy not in {"balanced", "round_robin", "quota_weighted", "least_used"}:
            problems.append(
                f"HYDRA_STRATEGY={self.strategy!r} is not one of "
                "balanced, round_robin, quota_weighted, least_used"
            )
        if not 0.0 <= self.balance_floor <= 1.0:
            problems.append(
                f"HYDRA_BALANCE_FLOOR={self.balance_floor} must be between 0 and 1 -- it is the "
                "share of traffic the most-used account keeps while the others catch up"
            )
        return problems

    def doq_problem(self) -> str:
        if not self.doq_enabled:
            return ""
        if not self.dot_base_domain:
            return "HYDRA_DOT_BASE_DOMAIN is unset, so no device hostname could be matched"
        if not self.dot_cert_file or not self.dot_key_file:
            return "HYDRA_DOT_CERT and HYDRA_DOT_KEY must both point at the wildcard certificate"
        for label, path in (("certificate", self.dot_cert_file), ("key", self.dot_key_file)):
            if not Path(path).is_file():
                return f"DoQ {label} not found at {path}"
        return ""

    def dot_problem(self) -> str:
        if not self.dot_enabled:
            return ""
        if not self.dot_base_domain:
            return "HYDRA_DOT_BASE_DOMAIN is unset, so no device hostname could be matched"
        if not self.dot_cert_file or not self.dot_key_file:
            return "HYDRA_DOT_CERT and HYDRA_DOT_KEY must both point at the wildcard certificate"
        for label, path in (("certificate", self.dot_cert_file), ("key", self.dot_key_file)):
            if not Path(path).is_file():
                return f"DoT {label} not found at {path}"
        return ""

    def warnings(self) -> list[str]:
        notes: list[str] = []
        if not self.accounts:
            notes.append(
                "HYDRA_ADGUARD_ACCOUNTS is empty -- the resolver has no account to forward "
                "to, so nothing is being filtered until at least one is configured"
            )
        if not self.seed_devices and not self.allow_anonymous and not self.store_url:
            notes.append(
                "HYDRA_DEVICES is empty, no device storage is configured and anonymous "
                "access is off -- no client could be authorised to resolve"
            )
        return notes


settings = Settings()
