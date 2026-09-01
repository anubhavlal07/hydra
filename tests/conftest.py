from __future__ import annotations

import json
import os

os.environ.setdefault("HYDRA_RESOLVER_SECRET", "test-secret-that-is-long-enough")
os.environ.setdefault("HYDRA_DASHBOARD_PASSWORD", "hunter2")
os.environ.setdefault("HYDRA_SESSION_SECRET", "test-session-secret")
os.environ.setdefault("HYDRA_DEVICES", "laptop:Work Laptop,phone:Pixel")
os.environ.setdefault("HYDRA_PUBLIC_API_BASE", "https://api.dns.example.test")
os.environ.setdefault("HYDRA_STORE_URL", "")
os.environ.setdefault("HYDRA_STORE_KEY", "")
os.environ.setdefault("HYDRA_STORE_SECRET", "")
os.environ.setdefault("HYDRA_APP_CATALOG_URL", "")
os.environ.setdefault(
    "HYDRA_ADGUARD_ACCOUNTS",
    json.dumps(
        [
            {"label": "one", "username": "first@example.test", "password": "pw1"},
            {"label": "two", "username": "second@example.test", "password": "pw2"},
        ]
    ),
)

import dns.message
import dns.rdatatype
import dns.rrset
import pytest


@pytest.fixture
def query_wire() -> bytes:
    return dns.message.make_query("example.com", dns.rdatatype.A).to_wire()


def make_answer(
    name: str = "example.com",
    address: str = "93.184.216.34",
    ttl: int = 300,
    rcode: int = 0,
) -> tuple[dns.message.Message, bytes]:
    query = dns.message.make_query(name, dns.rdatatype.A)
    response = dns.message.make_response(query)
    response.set_rcode(rcode)
    if rcode == 0:
        response.answer.append(dns.rrset.from_text(f"{name}.", ttl, "IN", "A", address))
    wire = response.to_wire()
    return response, wire
