from __future__ import annotations

import pytest

from hydra_dns.adguard import policy

BASE = {
    "protection_enabled": True,
    "block_ttl_seconds": 30,
    "blocking_mode_settings": {"blocking_mode": "REFUSED"},
    "safebrowsing_settings": {"enabled": True, "block_nrd": False},
    "parental_control_settings": {"enabled": False, "blocked_services": []},
    "filter_lists_settings": {
        "enabled": True,
        "filter_list": [
            {"filter_id": "adguard_dns_filter", "enabled": True},
            {"filter_id": "peter_lowe_list", "enabled": False},
        ],
    },
    "user_rules_settings": {"enabled": True, "rules": ["||ads.example^"], "rules_count": 1},
}


def settings(**overrides):
    merged = policy._deep_copy(BASE)
    merged.update(overrides)
    return merged


def test_a_trailing_carriage_return_is_not_a_different_rule():
    assert policy.normalise_rule("||ads.example^\r") == policy.normalise_rule("||ads.example^")


def test_parsing_a_textarea_drops_blanks_and_duplicates():
    rules = policy.parse_rules("||a.example^\n\n  ||b.example^  \n||a.example^\n")
    assert rules == ["||a.example^", "||b.example^"]


def test_comments_are_kept():
    assert policy.parse_rules("! ads\n||a.example^") == ["! ads", "||a.example^"]


def test_rules_are_read_in_order_without_blanks():
    raw = settings(user_rules_settings={"rules": ["||a^", "", "  ", "||b^\r"], "enabled": True})
    assert policy.rules_of(raw) == ["||a^", "||b^"]


def test_a_block_rule_anchors_the_domain():
    assert policy.block_rule("Ads.Example.COM") == "||ads.example.com^"


def test_an_allow_rule_is_the_exception_form():
    assert policy.allow_rule("example.com") == "@@||example.com^"


@pytest.mark.parametrize(
    "bad", ["https://example.com/x", "not a domain", "", "||example.com^", "localhost"]
)
def test_nonsense_is_refused_rather_than_written(bad):
    with pytest.raises(ValueError):
        policy.block_rule(bad)


def test_the_targeted_domain_can_be_read_back():
    assert policy.rule_targets("@@||example.com^") == "example.com"
    assert policy.rule_targets("address=/dev.local/192.168.1.50") == ""


def test_a_rule_missing_from_one_account_is_drift():
    diff = policy.diff_rules({"a": ["||x^"], "b": ["||x^"], "c": []})
    assert diff["in_sync"] is False
    assert diff["rules"][0]["accounts"] == ["a", "b"]
    assert diff["rules"][0]["everywhere"] is False


def test_identical_accounts_are_in_sync():
    diff = policy.diff_rules({"a": ["||x^", "||y^"], "b": ["||y^", "||x^"]})
    assert diff["in_sync"] is True


def test_the_union_keeps_first_seen_order():
    diff = policy.diff_rules({"a": ["||z^", "||a^"], "b": ["||z^", "||a^"]})
    assert [r["rule"] for r in diff["rules"]] == ["||z^", "||a^"]


def test_an_account_with_protection_off_is_called_out():
    summary = policy.summarise({"a": settings(), "b": settings(protection_enabled=False)})
    assert summary["unprotected_accounts"] == ["b"]
    assert "protection" in summary["drifting"]
    assert summary["in_sync"] is False


def test_differing_filter_lists_are_drift():
    other = policy._deep_copy(BASE)
    other["filter_lists_settings"]["filter_list"][1]["enabled"] = True
    summary = policy.summarise({"a": settings(), "b": other})
    assert "filter lists" in summary["drifting"]


def test_matching_accounts_report_in_sync():
    summary = policy.summarise({"a": settings(), "b": settings()})
    assert summary["in_sync"] is True
    assert summary["drifting"] == []
    assert summary["unprotected_accounts"] == []


def test_writing_rules_leaves_every_other_setting_alone():
    body = policy.apply_to_settings(settings(), rules=["||new^"])
    assert body["blocking_mode_settings"] == {"blocking_mode": "REFUSED"}
    assert body["block_ttl_seconds"] == 30
    assert body["safebrowsing_settings"]["block_nrd"] is False
    assert body["user_rules_settings"]["rules"] == ["||new^"]


def test_the_derived_rule_count_is_not_sent_back():
    body = policy.apply_to_settings(settings(), rules=["||new^"])
    assert "rules_count" not in body["user_rules_settings"]


def test_the_source_settings_are_not_mutated():
    original = settings()
    policy.apply_to_settings(original, rules=["||new^"], protection=False)
    assert original["user_rules_settings"]["rules"] == ["||ads.example^"]
    assert original["protection_enabled"] is True


def test_syncing_filter_lists_enables_and_disables():
    body = policy.apply_to_settings(settings(), filter_lists=["peter_lowe_list"])
    states = {f["filter_id"]: f["enabled"] for f in body["filter_lists_settings"]["filter_list"]}
    assert states == {"adguard_dns_filter": False, "peter_lowe_list": True}


def test_a_list_the_account_has_never_seen_is_added():
    body = policy.apply_to_settings(settings(), filter_lists=["hagezi_multinormal"])
    states = {f["filter_id"]: f["enabled"] for f in body["filter_lists_settings"]["filter_list"]}
    assert states["hagezi_multinormal"] is True


def test_passing_nothing_changes_nothing():
    assert policy.apply_to_settings(settings())["user_rules_settings"]["rules"] == [
        "||ads.example^"
    ]


def test_blocking_a_domain_removes_its_existing_allow_rule():
    rules = policy.with_rule(["@@||x.example^", "||keep^"], policy.block_rule("x.example"))
    assert rules == ["||keep^", "||x.example^"]


def test_blocking_something_already_blocked_does_not_duplicate_it():
    rules = policy.with_rule(["||x.example^"], policy.block_rule("x.example"))
    assert rules == ["||x.example^"]


def test_unrelated_rules_for_other_domains_survive():
    rules = policy.with_rule(["||other.example^"], policy.block_rule("x.example"))
    assert rules == ["||other.example^", "||x.example^"]


def test_removing_a_rule_ignores_whitespace_differences():
    assert policy.without_rule(["||x^\r", "||y^"], "||x^") == ["||y^"]


def test_safe_browsing_and_parental_control_can_be_written():
    body = policy.apply_to_settings(settings(), safe_browsing=False, parental_control=True)
    assert body["safebrowsing_settings"]["enabled"] is False
    assert body["parental_control_settings"]["enabled"] is True


def test_writing_one_flag_leaves_the_rest_of_its_section_alone():
    body = policy.apply_to_settings(settings(), safe_browsing=False)
    assert body["safebrowsing_settings"]["block_nrd"] is False
    assert body["parental_control_settings"]["blocked_services"] == []


def test_youtube_restricted_mode_is_written_without_touching_the_rest_of_parental_control():
    current = settings()
    current["parental_control_settings"] = {
        "enabled": True,
        "youtube_safe_search_enabled": True,
        "engines_safe_search_enabled": True,
        "blocked_services": ["tiktok"],
    }
    body = policy.apply_to_settings(current, youtube_safe_search=False)
    section = body["parental_control_settings"]
    assert section["youtube_safe_search_enabled"] is False
    assert section["engines_safe_search_enabled"] is True
    assert section["enabled"] is True
    assert section["blocked_services"] == ["tiktok"]


def test_accounts_disagreeing_on_youtube_restricted_mode_show_as_drift():
    on = settings()
    on["parental_control_settings"] = {"enabled": True, "youtube_safe_search_enabled": True}
    off = settings()
    off["parental_control_settings"] = {"enabled": True, "youtube_safe_search_enabled": False}

    summary = policy.summarise({"alpha": off, "bravo": off, "charlie": on})

    assert "YouTube restricted mode" in summary["drifting"]
    assert summary["outliers"] == {"charlie": ["YouTube restricted mode"]}
    assert summary["youtube_safe_search"]["per_account"]["charlie"] is True
