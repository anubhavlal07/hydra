from __future__ import annotations

import json
import os

import pytest

from hydra_dns.accounts_store import AccountStore, AccountStoreError, derive_label
from hydra_dns.config import AccountConfig

SEED = [
    AccountConfig(label="one", username="first@example.test", password="pw1"),
    AccountConfig(label="two", username="second@example.test", password="pw2"),
]


def point_at(monkeypatch, path: str) -> None:
    import dataclasses

    from hydra_dns import accounts_store

    monkeypatch.setattr(
        accounts_store,
        "settings",
        dataclasses.replace(accounts_store.settings, accounts_file=path),
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    point_at(monkeypatch, str(tmp_path / "accounts.json"))
    return AccountStore(SEED)


def test_a_label_is_the_first_free_callsign():
    assert derive_label(set()) == "alpha"
    assert derive_label({"alpha", "charlie"}) == "bravo"


def test_callsigns_wrap_with_a_suffix_once_all_are_taken():
    from hydra_dns.accounts_store import CALLSIGNS

    assert derive_label(set(CALLSIGNS)) == "alpha2"


def test_an_email_derived_label_is_renamed_on_load(store, tmp_path):
    (tmp_path / "accounts.json").write_text(
        json.dumps(
            {
                "accounts": [
                    {"label": "kiran", "username": "kiran4821@example.test", "password": "p"},
                    {"label": "kiran2", "username": "kiran.x@example.test", "password": "p"},
                    {"label": "main", "username": "main@example.test", "password": "p"},
                    {"label": "alpha", "username": "kept@example.test", "password": "p"},
                ]
            }
        )
    )
    assert [a.label for a in store.load()] == ["bravo", "charlie", "delta", "alpha"]
    written = json.loads((tmp_path / "accounts.json").read_text())
    assert [a["label"] for a in written["accounts"]] == ["bravo", "charlie", "delta", "alpha"]


def test_a_first_run_adopts_the_seed_and_writes_it(store, tmp_path):
    assert [a.label for a in store.load()] == ["one", "two"]
    written = json.loads((tmp_path / "accounts.json").read_text())
    assert [a["username"] for a in written["accounts"]] == [
        "first@example.test",
        "second@example.test",
    ]


def test_the_file_wins_over_the_seed_once_it_exists(store, tmp_path):
    (tmp_path / "accounts.json").write_text(
        json.dumps(
            {"accounts": [{"label": "only", "username": "sole@example.test", "password": "p"}]}
        )
    )
    assert [a.label for a in store.load()] == ["only"]


def test_a_corrupt_file_falls_back_without_overwriting_it(store, tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text("{ not json")
    assert [a.label for a in store.load()] == ["one", "two"]
    assert path.read_text() == "{ not json"
    assert store.last_error


def test_an_empty_stored_list_is_treated_as_damage(store, tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": []}))
    assert [a.label for a in store.load()] == ["one", "two"]


def test_adding_persists_and_derives_a_label(store, tmp_path):
    store.load()
    added = store.add("kiran4821@example.test", "secret")
    assert added.label == "alpha"

    reloaded = AccountStore(SEED)
    assert [a.label for a in reloaded.load()] == ["one", "two", "alpha"]


def test_a_duplicate_address_is_refused(store):
    store.load()
    with pytest.raises(AccountStoreError, match="already in the pool"):
        store.add("FIRST@example.test", "pw")


def test_a_nonsense_address_is_refused(store):
    store.load()
    with pytest.raises(AccountStoreError):
        store.add("not-an-email", "pw")


def test_an_explicit_label_is_validated(store):
    store.load()
    with pytest.raises(AccountStoreError, match="lowercase"):
        store.add("x@example.test", "pw", label="Not A Label")


def test_an_explicit_label_that_is_taken_is_refused(store):
    store.load()
    with pytest.raises(AccountStoreError, match="already used"):
        store.add("x@example.test", "pw", label="one")


@pytest.mark.skipif(os.name != "posix", reason="Windows does not honour POSIX file modes")
def test_the_file_is_not_world_readable(store, tmp_path):
    import stat

    store.load()
    mode = stat.S_IMODE((tmp_path / "accounts.json").stat().st_mode)
    assert not mode & 0o077, f"accounts file is {oct(mode)}"


def test_removing_persists(store):
    store.load()
    store.remove("two")
    assert [a.label for a in AccountStore(SEED).load()] == ["one"]


def test_the_last_account_cannot_be_removed(store):
    store.load()
    store.remove("two")
    with pytest.raises(AccountStoreError, match="only account left"):
        store.remove("one")
    assert len(store.accounts) == 1


def test_removing_something_absent_says_so(store):
    store.load()
    with pytest.raises(AccountStoreError, match="no account is labelled"):
        store.remove("nope")


def test_without_a_file_the_list_is_read_only(monkeypatch):
    point_at(monkeypatch, "")
    store = AccountStore(SEED)
    assert store.configured is False
    assert store.snapshot()["editable"] is False
    with pytest.raises(AccountStoreError, match="only be changed in"):
        store.add("x@example.test", "pw")


def test_seeding_hands_ownership_to_the_file(store):
    store.load()
    assert store.snapshot()["from_file"] is True


def test_turning_an_account_off_persists(store):
    store.load()
    assert store.set_enabled("two", False).enabled is False

    reloaded = AccountStore(SEED)
    reloaded.load()
    assert [(a.label, a.enabled) for a in reloaded.accounts] == [("one", True), ("two", False)]


def test_turning_it_back_on_persists(store):
    store.load()
    store.set_enabled("two", False)
    store.set_enabled("two", True)

    reloaded = AccountStore(SEED)
    reloaded.load()
    assert all(a.enabled for a in reloaded.accounts)


def test_the_last_enabled_account_cannot_be_turned_off(store):
    store.load()
    store.set_enabled("two", False)
    with pytest.raises(AccountStoreError, match="only account still taking traffic"):
        store.set_enabled("one", False)
    assert store.enabled_count == 1


def test_the_last_enabled_account_cannot_be_removed_either(store):
    store.load()
    store.set_enabled("two", False)
    with pytest.raises(AccountStoreError, match="only account still taking traffic"):
        store.remove("one")
    assert [a.label for a in store.accounts] == ["one", "two"]


def test_a_disabled_account_can_still_be_removed(store):
    store.load()
    store.set_enabled("two", False)
    store.remove("two")
    assert [a.label for a in store.accounts] == ["one"]


def test_toggling_something_absent_says_so(store):
    store.load()
    with pytest.raises(AccountStoreError, match="no account is labelled"):
        store.set_enabled("nope", False)


def test_the_snapshot_counts_how_many_are_taking_traffic(store):
    store.load()
    store.set_enabled("two", False)
    snapshot = store.snapshot()
    assert snapshot["count"] == 2
    assert snapshot["enabled_count"] == 1
    assert snapshot["min_enabled"] == 1


def test_without_a_file_the_toggle_is_read_only(monkeypatch):
    point_at(monkeypatch, "")
    store = AccountStore(SEED)
    with pytest.raises(AccountStoreError, match="only be changed in"):
        store.set_enabled("one", False)


def test_an_account_stored_before_the_toggle_existed_is_on(store, tmp_path):
    (tmp_path / "accounts.json").write_text(
        json.dumps(
            {"accounts": [{"label": "old", "username": "old@example.test", "password": "p"}]}
        )
    )
    assert store.load()[0].enabled is True
