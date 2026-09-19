from __future__ import annotations

import dataclasses
import time

import pytest

from hydra_dns import runtime, watch
from hydra_dns.config import settings

ACCOUNTS = [{"label": "one", "auth": {"authenticated": True}, "upstream": {"available": True}}]
QUOTA = {"requests_remaining": 500_000, "requests_used": 10}
POOL = {"spent": False, "fallback": {"active": False}}


def reasons(**extra) -> list[str]:
    return watch.reasons(accounts=ACCOUNTS, quota=QUOTA, pool_state=POOL, warn_days=5, **extra)


def test_a_healthy_resolver_says_nothing() -> None:
    assert reasons() == []


def test_a_registry_that_has_gone_quiet_is_reported() -> None:
    stale = {"configured": True, "using_seed": False, "last_sync_at": time.time() - 3600}
    found = reasons(registry=stale)
    assert any("device registry has not been readable" in r for r in found)
    assert any("changes to them will not apply" in r for r in found)


def test_a_registry_that_synced_recently_is_not() -> None:
    fresh = {"configured": True, "using_seed": False, "last_sync_at": time.time() - 60}
    assert reasons(registry=fresh) == []


def test_a_registry_nobody_configured_is_not_a_problem() -> None:
    assert reasons(registry={"configured": False, "last_sync_at": 0}) == []


def test_a_registry_running_on_the_seed_list_is_reported_elsewhere() -> None:
    seeded = {"configured": True, "using_seed": True, "last_sync_at": time.time() - 9999}
    assert reasons(registry=seeded) == []


def test_queries_failing_in_bulk_is_reported() -> None:
    found = reasons(resolver={"queries": 1000, "errors": 250})
    assert any("25% of queries are failing" in r for r in found)


def test_a_few_failures_are_not_worth_waking_someone_for() -> None:
    assert reasons(resolver={"queries": 1000, "errors": 20}) == []


def test_a_quiet_resolver_does_not_trip_on_one_bad_query() -> None:
    assert reasons(resolver={"queries": 3, "errors": 3}) == []


def test_repeated_failed_sign_ins_are_reported() -> None:
    found = reasons(sign_in_failures=9)
    assert any("9 failed dashboard sign-ins" in r for r in found)
    assert any("trying passwords" in r for r in found)


def test_one_mistyped_password_is_not_an_alert() -> None:
    assert reasons(sign_in_failures=2) == []


@pytest.mark.parametrize(
    "seconds,expected", [(120, "2 minutes"), (7200, "2 hours"), (172800, "2.0 days")]
)
def test_a_gap_is_described_in_units_a_person_reads(seconds: int, expected: str) -> None:
    now = time.time()
    assert runtime.describe_gap(now - seconds, now) == expected


def test_an_unknown_gap_says_so() -> None:
    assert runtime.describe_gap(None) == "an unknown time"


def test_a_clean_shutdown_leaves_nothing_to_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        runtime, "settings", dataclasses.replace(settings, cache_file=str(tmp_path / "cache.json"))
    )
    assert runtime.claim() is None
    runtime.release()
    assert runtime.claim() is None


def test_a_crash_is_noticed_on_the_next_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        runtime, "settings", dataclasses.replace(settings, cache_file=str(tmp_path / "cache.json"))
    )
    runtime.claim()
    started = runtime.claim()

    assert started is not None
    assert abs(started - time.time()) < 5


def test_no_state_directory_means_the_check_is_skipped(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime,
        "settings",
        dataclasses.replace(settings, cache_file="", device_cache_file="", accounts_file=""),
    )
    assert runtime.marker_path() is None
    assert runtime.claim() is None
    runtime.release()


def test_an_unreadable_marker_does_not_stop_the_resolver(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        runtime, "settings", dataclasses.replace(settings, cache_file=str(tmp_path / "cache.json"))
    )
    (tmp_path / runtime.MARKER_NAME).write_text("running not-a-number", encoding="utf-8")
    assert runtime.claim() is None
