from __future__ import annotations

import dataclasses

import pytest
import respx
from test_doh import client, mock_upstream

from hydra_dns.api import setup as setup_api
from hydra_dns.config import settings
from hydra_dns.policy_store import diff, settings_diff, settings_of
from hydra_dns.store import Device, normalise_tags

PASSWORD = "hunter2"


async def signed_in(http):
    await http.post("/api/login", json={"password": PASSWORD})


class FakeStore:
    def __init__(self, devices: list[Device]) -> None:
        self._devices = {d.id: d for d in devices}
        self.configured = True
        self.writable = True
        self.bulk_calls: list[dict] = []
        self.last_error = ""

    @property
    def devices(self) -> list[Device]:
        return list(self._devices.values())

    def get(self, device_id: str) -> Device | None:
        return self._devices.get(device_id)

    def tags(self) -> list[str]:
        return sorted({tag for d in self._devices.values() for tag in d.tags})

    def snapshot(self) -> dict:
        return {"configured": True, "writable": True, "count": len(self._devices)}

    async def set_tags(self, device_id: str, values: list[str]) -> Device:
        tags = normalise_tags(values)
        updated = dataclasses.replace(self._devices[device_id], tags=tags)
        self._devices[device_id] = updated
        return updated

    async def rename(self, device_id: str, name: str) -> Device:
        updated = dataclasses.replace(self._devices[device_id], name=name)
        self._devices[device_id] = updated
        return updated

    async def set_policy(
        self,
        device_id: str,
        safe_search: bool,
        block_mode: str,
        block_address: str,
        safe_search_skip: list[str] | None = None,
    ) -> Device:
        current = self._devices[device_id]
        updated = dataclasses.replace(
            current,
            safe_search=safe_search,
            block_mode=block_mode,
            block_address=block_address,
            safe_search_skip=current.safe_search_skip
            if safe_search_skip is None
            else tuple(safe_search_skip),
        )
        self._devices[device_id] = updated
        return updated

    async def set_budget(self, device_id: str, hourly: int) -> Device:
        updated = dataclasses.replace(self._devices[device_id], hourly_budget=int(hourly))
        self._devices[device_id] = updated
        return updated

    async def set_rules(self, device_id: str, lines: list[str]) -> Device:
        updated = dataclasses.replace(self._devices[device_id], rules=tuple(lines))
        self._devices[device_id] = updated
        return updated

    async def add(self, name: str, device_id: str | None = None) -> Device:
        created = Device(id=device_id or name.lower().replace(" ", "-"), name=name)
        self._devices[created.id] = created
        return created

    async def bulk_apply(self, device_ids, *, tags=None, rules=None, policy=None) -> dict:
        self.bulk_calls.append({"ids": list(device_ids), "tags": tags, "rules": rules})
        applied, failed = [], {}
        for device_id in device_ids:
            if device_id in self._devices:
                if tags is not None:
                    self._devices[device_id] = dataclasses.replace(
                        self._devices[device_id], tags=normalise_tags(tags)
                    )
                applied.append(device_id)
            else:
                failed[device_id] = "no such device"
        return {"applied": applied, "failed": failed}


@pytest.fixture
def fake_devices(monkeypatch):
    store = FakeStore(
        [
            Device(id="laptop", name="Work Laptop"),
            Device(id="phone", name="Pixel", tags=("personal",)),
        ]
    )
    monkeypatch.setattr(setup_api, "_store", lambda request: store)
    return store


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["Kids", "kids", "KIDS"], ("kids",)),
        (["work laptop"], ("work laptop",)),
        (["has_underscore", "has-dash"], ("has_underscore", "has-dash")),
        (["-bad", "way too long a tag to ever be accepted here"], ()),
        (["  spaced   out  "], ("spaced out",)),
        ("not a list", ()),
    ],
)
def test_tags_are_normalised(raw, expected) -> None:
    assert normalise_tags(raw) == expected


def test_only_ten_tags_survive() -> None:
    assert len(normalise_tags([f"tag{i}" for i in range(50)])) == 10


@respx.mock
async def test_tags_can_be_written_and_come_back(fake_devices) -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.put("/api/devices/laptop/tags", json={"tags": ["Kids", "shared"]})

    assert response.status_code == 200
    assert response.json()["tags"] == ["kids", "shared"]


@respx.mock
async def test_the_device_list_carries_the_tag_vocabulary(fake_devices) -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        payload = (await http.get("/api/devices")).json()

    assert payload["tags"] == ["personal"]
    assert all("tags" in device for device in payload["devices"])


def test_every_device_field_reaches_the_dashboard() -> None:
    device = Device(
        id="moto",
        name="Moto Edge 60",
        rules=("ads.example",),
        safe_search=True,
        block_mode="address",
        block_address="10.0.0.1",
        tags=("personal",),
        hourly_budget=5000,
    )

    payload = setup_api._device_payload(device)

    missing = [f.name for f in dataclasses.fields(Device) if f.name not in payload]
    assert not missing, f"the dashboard never sees {missing}"
    assert payload["hourly_budget"] == 5000
    assert payload["tags"] == ["personal"]


@respx.mock
async def test_every_write_endpoint_answers_with_the_whole_device(fake_devices) -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        answers = [
            (await http.put("/api/devices/phone/budget", json={"hourly_budget": 5000})).json(),
            (await http.patch("/api/devices/phone", json={"name": "Moto Edge 60"})).json(),
            (await http.put("/api/devices/phone/tags", json={"tags": ["personal"]})).json(),
            (await http.put("/api/devices/phone/rules", json={"rules": ["ads.example"]})).json(),
            (
                await http.put(
                    "/api/devices/phone/policy",
                    json={"safe_search": True, "block_mode": "zero", "block_address": ""},
                )
            ).json(),
        ]

    for answer in answers:
        missing = [f.name for f in dataclasses.fields(Device) if f.name not in answer]
        assert not missing, f"a write endpoint answered without {missing}"
        assert answer["hourly_budget"] == 5000


@respx.mock
async def test_an_hourly_ceiling_comes_back_on_the_device_it_was_written_to(fake_devices) -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        written = await http.put("/api/devices/phone/budget", json={"hourly_budget": 2500})
        listed = (await http.get("/api/devices")).json()

    assert written.json()["hourly_budget"] == 2500
    by_id = {d["id"]: d for d in listed["devices"]}
    assert by_id["phone"]["hourly_budget"] == 2500
    assert by_id["laptop"]["hourly_budget"] == 0


@respx.mock
async def test_a_bulk_change_reaches_every_named_device(fake_devices) -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.post(
            "/api/devices/bulk", json={"devices": ["laptop", "phone"], "tags": ["kids"]}
        )

    body = response.json()
    assert body["applied"] == ["laptop", "phone"]
    assert body["failed"] == {}


@respx.mock
async def test_a_bulk_change_names_the_devices_it_could_not_find(fake_devices) -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.post(
            "/api/devices/bulk", json={"devices": ["laptop", "ghost"], "tags": ["kids"]}
        )

    body = response.json()
    assert body["applied"] == ["laptop"]
    assert "ghost" in body["failed"]


@respx.mock
async def test_a_bulk_change_is_capped(fake_devices, monkeypatch) -> None:
    monkeypatch.setattr(setup_api, "settings", dataclasses.replace(settings, bulk_max_devices=2))
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.post(
            "/api/devices/bulk", json={"devices": ["a", "b", "c"], "tags": ["x"]}
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "too_many"


@respx.mock
async def test_a_bulk_change_needs_something_to_change(fake_devices) -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.post("/api/devices/bulk", json={"devices": ["laptop"]})

    assert response.status_code == 400


def test_a_csv_row_is_split_on_real_commas_only() -> None:
    assert setup_api._split_csv_line('a,"b,c",d') == ["a", "b,c", "d"]
    assert setup_api._split_csv_line('a,"say ""hi""",c') == ["a", 'say "hi"', "c"]
    assert setup_api._split_csv_line("") == [""]


def test_a_csv_cell_is_quoted_only_when_it_has_to_be() -> None:
    assert setup_api._csv_cell("plain") == "plain"
    assert setup_api._csv_cell("has,comma") == '"has,comma"'
    assert setup_api._csv_cell('has"quote') == '"has""quote"'
    assert setup_api._csv_cell(None) == ""


def test_an_import_reads_names_tags_and_rules() -> None:
    rows = setup_api._parse_device_csv(
        "id,name,tags,rules\nlaptop,Work Laptop,kids shared,ads.example\n,Spare,,\n"
    )
    assert rows[0] == {
        "id": "laptop",
        "name": "Work Laptop",
        "tags": ["kids", "shared"],
        "rules": ["ads.example"],
    }
    assert rows[1]["id"] == ""
    assert rows[1]["name"] == "Spare"


def test_an_import_without_a_name_column_is_refused() -> None:
    assert setup_api._parse_device_csv("id,label\nlaptop,Work\n") == []


def test_an_import_of_only_a_header_finds_nothing() -> None:
    assert setup_api._parse_device_csv("id,name\n") == []


def test_an_import_skips_rows_with_no_name() -> None:
    assert setup_api._parse_device_csv("id,name\nlaptop,\n,\n") == []


@respx.mock
async def test_an_import_can_be_rehearsed(fake_devices) -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.post(
            "/api/devices/import",
            json={"csv": "id,name\nlaptop,Work Laptop\nspare,Spare\n", "dry_run": True},
        )

    body = response.json()
    assert body["dry_run"] is True
    assert body["would_add"] == ["spare"]
    assert body["would_update"] == ["laptop"]


@respx.mock
async def test_an_import_adds_what_is_missing(fake_devices) -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.post(
            "/api/devices/import", json={"csv": "id,name,tags\nspare,Spare,kids\n"}
        )

    body = response.json()
    assert body["added"] == ["spare"]
    assert fake_devices.get("spare").tags == ("kids",)


@respx.mock
async def test_devices_export_as_csv(fake_devices) -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.get("/api/devices/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().split("\n")
    assert lines[0] == "id,name,tags,rules,safe_search,block_mode,block_address"
    assert any(line.startswith("laptop,Work Laptop") for line in lines)


def test_a_rule_diff_names_what_moved() -> None:
    changed = diff(["a", "b", "c"], ["b", "c", "d"])
    assert changed["added"] == ["d"]
    assert changed["removed"] == ["a"]
    assert changed["unchanged"] == 2
    assert changed["changed"] is True


def test_an_unchanged_ruleset_says_so() -> None:
    assert diff(["a", "b"], ["a", "b"])["changed"] is False


def test_a_rule_diff_ignores_duplicates() -> None:
    assert diff(["a", "a"], ["a"])["changed"] is False


def test_a_settings_diff_names_only_what_moved() -> None:
    rows = settings_diff({"protection": True, "safe_browsing": True}, {"protection": False})
    assert rows == [{"field": "protection", "from": True, "to": False}]


def test_settings_are_read_out_of_a_policy_summary() -> None:
    summary = {
        "protection": {"value": True, "in_sync": True},
        "safe_browsing": {"value": False, "in_sync": True},
        "filter_lists": {"value": ["a"], "in_sync": True},
        "parental_control": {"value": None, "in_sync": False},
    }
    assert settings_of(summary) == {
        "protection": True,
        "safe_browsing": False,
        "filter_lists": ["a"],
        "parental_control": None,
    }


@respx.mock
async def test_the_alerts_endpoint_reports_that_nothing_is_wired() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        payload = (await http.get("/api/alerts")).json()

    assert payload["webhook_set"] is False
    assert payload["alerts"]["configured"] is False


@respx.mock
async def test_a_test_alert_without_a_webhook_is_refused() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.post("/api/alerts/test")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "no_webhook"


@respx.mock
async def test_tokens_need_the_registry() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        listing = await http.get("/api/tokens")
        created = await http.post("/api/tokens", json={"name": "ci"})

    assert listing.status_code == 200
    assert listing.json()["tokens"] == []
    assert created.status_code == 503
    assert created.json()["detail"]["code"] == "tokens_unavailable"


@respx.mock
async def test_policy_versions_report_themselves_unavailable_without_the_registry() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        payload = (await http.get("/api/policy/versions")).json()

    assert payload["available"] is False
    assert payload["versions"] == []


@respx.mock
async def test_exporting_history_without_postgres_is_refused() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.get("/api/query-log/export", params={"source": "history"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "history_unavailable"


@respx.mock
async def test_the_live_log_exports_as_ndjson() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.get("/api/query-log/export", params={"source": "live"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in response.headers["content-disposition"]


@respx.mock
async def test_the_live_log_exports_as_csv_with_a_header() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.get(
            "/api/query-log/export", params={"source": "live", "format": "csv"}
        )

    assert response.text.splitlines()[0] == "at,device,name,type,rcode,cached,blocked,upstream"


@respx.mock
async def test_a_purge_needs_a_device() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.delete("/api/query-log")

    assert response.status_code in (400, 503)
