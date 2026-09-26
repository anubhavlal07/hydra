from __future__ import annotations

import json

import pytest

from hydra_dns import blocklists
from hydra_dns.blocklists import Blocklist, ListState, Mirror, parse
from hydra_dns.rules import RuleSet, listed
from hydra_dns.store import Device, RuleGroup


def test_a_plain_list_is_one_name_a_line() -> None:
    parsed = parse("example.com\nTracker.Example.NET.\n\n  ads.test  \n")

    assert parsed.names == {"example.com", "tracker.example.net", "ads.test"}
    assert parsed.skipped == 0


def test_a_hosts_file_names_what_follows_the_address() -> None:
    parsed = parse(
        "127.0.0.1 localhost\n"
        "::1 localhost ip6-localhost\n"
        "0.0.0.0 0.0.0.0\n"
        "0.0.0.0 ads.example.com\n"
        "127.0.0.1\tpixel.example.org  # inline comment\n"
        "0.0.0.0 one.test two.test\n"
    )

    assert parsed.names == {"ads.example.com", "pixel.example.org", "one.test", "two.test"}


def test_an_adblock_list_keeps_domains_and_exceptions() -> None:
    parsed = parse("||ads.example.com^\n||tracker.test\n@@||good.ads.example.com^\n")

    assert parsed.names == {"ads.example.com", "tracker.test"}
    assert parsed.allow == {"good.ads.example.com"}


def test_comments_and_blank_lines_are_neither_names_nor_skips() -> None:
    parsed = parse("! Title: a list\n# a hosts comment\n\n   \nexample.com\n")

    assert parsed.names == {"example.com"}
    assert parsed.skipped == 0


@pytest.mark.parametrize(
    "rule",
    [
        "||example.com^$third-party",
        "||example.com/path^",
        "/ads[0-9]+/",
        "*.example.com",
        "||*.example.com^",
        "example.com##.banner",
        "|https://example.com^",
        "not a domain at all",
        "exa_mple.com",
        "192.0.2.1",
        "@@||example.com^$important",
    ],
)
def test_a_rule_a_resolver_cannot_apply_is_skipped_and_counted(rule: str) -> None:
    parsed = parse(f"good.test\n{rule}\n")

    assert parsed.names == {"good.test"}
    assert parsed.skipped == 1


def test_bytes_with_a_byte_order_mark_parse_like_text() -> None:
    parsed = parse("﻿example.com\r\nads.test\r\n".encode())

    assert parsed.names == {"example.com", "ads.test"}


def test_a_name_that_is_not_ascii_is_skipped_rather_than_guessed() -> None:
    parsed = parse("bücher.example\nexample.com\n".encode())

    assert parsed.names == {"example.com"}
    assert parsed.skipped == 1


def test_the_suffix_walk_covers_every_name_under_a_listed_one() -> None:
    names = frozenset({"example.com", "ads.test"})

    assert listed(names, "example.com") == "example.com"
    assert listed(names, "a.b.example.com") == "example.com"
    assert listed(names, "notexample.com") is None
    assert listed(names, "com") is None
    assert listed(frozenset(), "example.com") is None


def test_a_listed_top_level_domain_covers_the_whole_zone() -> None:
    assert listed(frozenset({"zip"}), "download.example.zip") == "zip"


def test_allows_reports_an_exception_and_only_an_exception() -> None:
    rules = RuleSet(block=("example.com",), allow=("safe.example.com",))

    assert rules.allows("safe.example.com")
    assert rules.allows("deep.safe.example.com.")
    assert not rules.allows("example.com")
    assert not RuleSet().allows("example.com")


def test_a_lists_own_exception_and_its_allow_beat_the_list() -> None:
    state = ListState(
        meta=Blocklist(id="ads", name="Ads", url="https://l.test/a", allow=("ok.test",))
    )
    state.hold(parse("||example.com^\n||ok.test^\n@@||cdn.example.com^\n"))

    assert state.blocks("www.example.com")
    assert not state.blocks("cdn.example.com")
    assert not state.blocks("img.cdn.example.com")
    assert not state.blocks("ok.test")


@pytest.mark.parametrize(
    ("tags", "device_tags"),
    [
        ((), ()),
        ((), ("kids",)),
        (("kids",), ("kids",)),
        (("kids",), ("work",)),
        (("kids", "tv"), ("tv", "work")),
        (("kids",), ()),
    ],
)
def test_tag_targeting_is_exactly_the_rule_group_rule(tags, device_tags) -> None:
    device = Device(id="tv", name="TV", tags=device_tags)
    group = RuleGroup(id="g", name="G", tags=tags)
    listing = Blocklist(id="l", name="L", url="https://l.test/", tags=tags)

    assert listing.applies_to(device) == group.applies_to(device)


def test_an_unknown_device_is_reached_only_by_an_untagged_list() -> None:
    assert Blocklist(id="l", name="L", url="https://l.test/").applies_to(None)
    assert not Blocklist(id="l", name="L", url="https://l.test/", tags=("kids",)).applies_to(None)


def test_a_row_that_is_not_https_or_has_a_bad_id_is_ignored() -> None:
    assert blocklists.from_row({"id": "ok", "name": "A", "url": "http://l.test/"}) is None
    assert blocklists.from_row({"id": "Bad Id", "name": "A", "url": "https://l.test/"}) is None
    assert blocklists.from_row({"id": "ok", "name": "A", "url": "https://" + "a" * 2050}) is None
    found = blocklists.from_row(
        {"id": "ok", "name": "A", "url": "https://l.test/", "allow": ["@@||X.test^", "bad name"]}
    )
    assert found is not None
    assert found.allow == ("x.test",)


def test_the_mirror_round_trips_names_exceptions_and_status(tmp_path) -> None:
    mirror = Mirror(str(tmp_path))
    state = ListState(meta=Blocklist(id="ads", name="Ads", url="https://l.test/a", tags=("kids",)))
    state.hold(parse("||example.com^\n@@||cdn.example.com^\nbad/rule\n"))
    state.etag = '"v1"'
    state.fetched_at = 1_700_000_000.0

    mirror.save(state.record(), state.names, state.own_allow)

    assert (tmp_path / "ads.txt").read_text().splitlines() == ["example.com", "@@cdn.example.com"]
    record = json.loads((tmp_path / "ads.json").read_text())
    assert record["names"] == 1
    assert record["skipped"] == 1
    assert record["etag"] == '"v1"'
    assert set(record) >= {
        "name",
        "url",
        "tags",
        "allow",
        "enabled",
        "etag",
        "last_modified",
        "fetched_at",
        "names",
        "skipped",
        "last_error",
    }

    [loaded] = Mirror(str(tmp_path)).load()
    assert loaded.meta == state.meta
    assert loaded.names == state.names
    assert loaded.own_allow == {"cdn.example.com"}
    assert loaded.skipped == 1
    assert loaded.fetched_at == 1_700_000_000.0
    assert loaded.blocks("www.example.com")
    assert not loaded.blocks("cdn.example.com")


def test_the_mirror_leaves_no_temporary_files_behind(tmp_path) -> None:
    mirror = Mirror(str(tmp_path))
    state = ListState(meta=Blocklist(id="ads", name="Ads", url="https://l.test/a"))
    state.hold(parse("example.com\n"))

    mirror.save(state.record(), state.names, state.own_allow)
    mirror.save(state.record(), state.names, state.own_allow)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["ads.json", "ads.txt"]


def test_the_mirror_forgets_a_list_completely(tmp_path) -> None:
    mirror = Mirror(str(tmp_path))
    state = ListState(meta=Blocklist(id="ads", name="Ads", url="https://l.test/a"))
    state.hold(parse("example.com\n"))
    mirror.save(state.record(), state.names, state.own_allow)

    mirror.forget("ads")

    assert list(tmp_path.iterdir()) == []
    assert mirror.load() == []


def test_a_broken_record_on_disk_is_skipped_not_fatal(tmp_path) -> None:
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "other.json").write_text(json.dumps({"id": "mismatch", "url": "https://l.test"}))

    assert Mirror(str(tmp_path)).load() == []


def test_a_mirror_without_a_directory_does_nothing() -> None:
    mirror = Mirror("")
    state = ListState(meta=Blocklist(id="ads", name="Ads", url="https://l.test/a"))

    mirror.save(state.record(), frozenset({"a.test"}), frozenset())
    mirror.forget("ads")
    assert not mirror.enabled
    assert mirror.load() == []
