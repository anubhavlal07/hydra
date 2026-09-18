from __future__ import annotations

import time

import dns.message
import dns.rdatatype
import pytest
from conftest import make_answer

from hydra_dns.resolver.cache import AnswerCache


def key_for(name: str = "example.com") -> tuple:
    return AnswerCache.key_for(dns.message.make_query(name, dns.rdatatype.A))


@pytest.fixture
def cache() -> AnswerCache:
    return AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600, stale_window_s=600)


def store(cache: AnswerCache, name: str = "example.com", ttl: int = 1) -> tuple:
    response, wire = make_answer(name=name, ttl=ttl)
    key = key_for(name)
    cache.put(key, response, wire)
    return key


def rewind(path: str, saved_at: float = 0.0, version: int | None = None) -> None:
    import json
    from pathlib import Path

    target = Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["saved_at"] += saved_at
    if version is not None:
        payload["version"] = version
    target.write_text(json.dumps(payload), encoding="utf-8")


def expire(cache: AnswerCache, key: tuple, by: float) -> None:
    cache._entries[key].expires_at = time.monotonic() - by


def test_an_expired_entry_is_kept_for_the_stale_window(cache) -> None:
    key = store(cache)
    expire(cache, key, 5)

    assert cache.get(key, 1) is None
    assert key in cache._entries


def test_a_stale_answer_is_served_when_asked_for_one(cache) -> None:
    key = store(cache)
    expire(cache, key, 5)

    wire = cache.get_stale(key, 4242)
    assert wire is not None

    answer = dns.message.from_wire(wire)
    assert answer.id == 4242
    assert answer.answer[0].ttl == 30
    assert cache.stats.stale_hits == 1


def test_a_live_entry_is_never_served_as_stale(cache) -> None:
    key = store(cache, ttl=300)
    assert cache.get_stale(key, 1) is None


def test_nothing_is_stale_once_the_window_has_passed(cache) -> None:
    key = store(cache)
    expire(cache, key, 601)

    assert cache.get_stale(key, 1) is None
    assert key not in cache._entries


def test_the_window_can_be_switched_off() -> None:
    cache = AnswerCache(max_entries=10, min_ttl=30, max_ttl=3600, stale_window_s=0)
    key = store(cache)
    expire(cache, key, 1)

    assert cache.get(key, 1) is None
    assert key not in cache._entries
    assert cache.get_stale(key, 1) is None


def test_expiry_is_counted_once_however_often_it_is_asked_for(cache) -> None:
    key = store(cache)
    expire(cache, key, 5)

    cache.get(key, 1)
    cache.get(key, 2)
    cache.get(key, 3)

    assert cache.stats.expired == 1


def test_purging_leaves_the_stale_window_alone(cache) -> None:
    key = store(cache)
    expire(cache, key, 5)

    assert cache.purge_expired() == 0
    assert key in cache._entries

    expire(cache, key, 601)
    assert cache.purge_expired() == 1


def test_the_snapshot_separates_live_from_stale(cache) -> None:
    live = store(cache, name="live.test", ttl=300)
    stale = store(cache, name="stale.test", ttl=1)
    expire(cache, stale, 5)

    snapshot = cache.snapshot()
    assert snapshot["entries"] == 2
    assert snapshot["live"] == 1
    assert snapshot["stale"] == 1
    assert snapshot["stale_window_s"] == 600
    assert live in cache._entries


def test_a_saved_cache_comes_back_after_a_restart(tmp_path, cache) -> None:
    store(cache, name="kept.test", ttl=300)
    path = str(tmp_path / "cache.json")

    assert cache.save(path) == 1

    revived = AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600, stale_window_s=600)
    assert revived.load(path) == 1

    wire = revived.get(key_for("kept.test"), 99)
    assert wire is not None
    assert dns.message.from_wire(wire).id == 99
    assert revived.stats.restored == 1


def test_a_saved_answer_keeps_ageing_while_the_process_is_down(tmp_path, cache) -> None:
    store(cache, name="ageing.test", ttl=300)
    path = str(tmp_path / "cache.json")
    cache.save(path)

    rewind(path, saved_at=-120)

    revived = AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600)
    revived.load(path)

    wire = revived.get(key_for("ageing.test"), 1)
    assert wire is not None
    assert dns.message.from_wire(wire).answer[0].ttl <= 180


def test_an_answer_that_expired_while_down_is_not_restored(tmp_path, cache) -> None:
    store(cache, name="gone.test", ttl=60)
    path = str(tmp_path / "cache.json")
    cache.save(path)

    rewind(path, saved_at=-3600)

    revived = AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600)
    assert revived.load(path) == 0


def test_a_snapshot_from_another_version_is_ignored(tmp_path, cache) -> None:
    store(cache)
    path = str(tmp_path / "cache.json")
    cache.save(path)

    rewind(path, version=999)

    assert AnswerCache(max_entries=10, min_ttl=30, max_ttl=3600).load(path) == 0


def test_a_missing_or_broken_snapshot_starts_cold(tmp_path) -> None:
    cache = AnswerCache(max_entries=10, min_ttl=30, max_ttl=3600)
    assert cache.load(str(tmp_path / "nothing.json")) == 0

    junk = tmp_path / "junk.json"
    junk.write_text("{not json", encoding="utf-8")
    assert cache.load(str(junk)) == 0
    assert cache.load("") == 0


def test_saving_is_capped_to_the_most_recently_used(tmp_path, cache) -> None:
    for index in range(10):
        store(cache, name=f"host{index}.test", ttl=300)

    path = str(tmp_path / "cache.json")
    assert cache.save(path, limit=3) == 3

    revived = AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600)
    revived.load(path)
    assert len(revived) == 3
    assert revived.has(key_for("host9.test"))
    assert not revived.has(key_for("host0.test"))


def test_has_does_not_move_the_hit_rate(cache) -> None:
    key = store(cache, ttl=300)
    assert cache.has(key)
    assert cache.stats.hits == 0
    assert cache.stats.misses == 0
