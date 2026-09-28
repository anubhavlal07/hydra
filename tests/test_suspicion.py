from __future__ import annotations

import pytest

from hydra_dns import suspicion
from hydra_dns.config import settings

GENERATED = [
    "xjw4k2qzp9v.com",
    "a1b2c3d4e5f6g7h8.top",
    "kqgdpbvnhwfcz.xyz",
    "qwkxzhtrmbv.net",
    "bqfwtgvtcnsw.ru",
    "3f2a9c1b7e5d.com",
    "x7k2m9q4w8z1.info",
]

ORDINARY = [
    "fonts.gstatic.com",
    "r3---sn-abc.googlevideo.com",
    "rr5---sn-4g5e6nzz.googlevideo.com",
    "d1234abcd.cloudfront.net",
    "d3c33hcgiwev3.cloudfront.net",
    "e6858.dscx.akamaiedge.net",
    "a23-45-67-89.deploy.static.akamaitechnologies.com",
    "ec2-3-120-45-6.eu-central-1.compute.amazonaws.com",
    "s3.us-east-1.amazonaws.com",
    "lh3.googleusercontent.com",
    "www.google.com",
    "stackoverflow.com",
    "office365.com",
    "login.microsoftonline.com",
    "v10.events.data.microsoft.com",
    "safebrowsing.googleapis.com",
    "web.whatsapp.com",
    "api.spotify.com",
    "i.ytimg.com",
    "ipv4-c001-del001-ix.1.oca.nflxvideo.net",
    "bbc.co.uk",
    "news.bbc.co.uk",
    "2mdn.net",
    "doubleclick.net",
    "wikipedia.org",
]


def flagged(name: str) -> bool:
    return suspicion.score(name).score >= settings.suspicious_score


@pytest.mark.parametrize("name", GENERATED)
def test_generated_looking_names_are_flagged(name: str) -> None:
    assert flagged(name), suspicion.score(name)


@pytest.mark.parametrize("name", ORDINARY)
def test_ordinary_names_are_not(name: str) -> None:
    assert not flagged(name), suspicion.score(name)


def test_a_random_label_says_why() -> None:
    verdict = suspicion.score("xjw4k2qzp9v.com")
    assert {"entropy", "digits", "consonants"} <= set(verdict.reasons)
    assert "tld" not in verdict.reasons


def test_an_abused_tld_is_a_reason_of_its_own() -> None:
    assert "tld" in suspicion.score("a1b2c3d4e5f6g7h8.top").reasons
    assert suspicion.score("a1b2c3d4e5f6g7h8.top").score == 100
    assert suspicion.score("shop.xyz").reasons == ("tld",)
    assert not flagged("shop.xyz")


def test_a_hex_label_is_named() -> None:
    assert "hex" in suspicion.score("3f2a9c1b7e5d.com").reasons


def test_punycode_counts_but_does_not_flag_alone() -> None:
    verdict = suspicion.score("xn--80ak6aa92e.com")
    assert verdict.reasons == ("punycode",)
    assert not flagged("xn--80ak6aa92e.com")
    assert "punycode" in suspicion.score("xn--e1afmkfd.xn--p1ai.example.com").reasons


def test_punycode_on_an_abused_tld_adds_up() -> None:
    verdict = suspicion.score("xn--pple-43d.top")
    assert set(verdict.reasons) == {"punycode", "tld"}


def test_a_very_long_subdomain_is_named() -> None:
    tunnel = "aGVsbG8gd29ybGQgdGhpcyBpcyBkYXRh.bG9uZ2VyIHBheWxvYWQgaGVyZQ.c2.example.com"
    verdict = suspicion.score(tunnel)
    assert "subdomain" in verdict.reasons
    assert "entropy" not in verdict.reasons


def test_many_labels_weigh_less_than_a_random_registrable_label() -> None:
    deep = suspicion.score("a.b.c.d.e.example.com")
    assert deep.reasons == ("labels",)
    assert deep.score < suspicion.score("xjw4k2qzp9v.com").score


def test_a_long_registrable_label_is_named() -> None:
    verdict = suspicion.score(("x" * 63) + ".com")
    assert "length" in verdict.reasons


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "localhost",
        "printer",
        "nas.local",
        "router.lan",
        "1.2.3.4",
        "4.3.2.1.in-addr.arpa",
        "b.a.9.8.7.6.5.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa",
    ],
)
def test_names_with_nothing_to_score_score_nothing(name: str) -> None:
    assert suspicion.score(name) == suspicion.CLEAN


def test_case_and_a_trailing_dot_change_nothing() -> None:
    assert suspicion.score("XJW4K2QZP9V.COM.") == suspicion.score("xjw4k2qzp9v.com")


def test_the_registrable_domain_understands_second_level_suffixes() -> None:
    assert suspicion.registrable("news.bbc.co.uk") == "bbc.co.uk"
    assert suspicion.registrable("a.b.example.com.") == "example.com"
    assert suspicion.registrable("co.uk") == "co.uk"
    assert suspicion.registrable("printer") == "printer"


def test_the_score_never_passes_a_hundred() -> None:
    worst = "xn--1.q.w.e.r.t.y.u.i.o.p.a.s.d.f.g.h.j.k.l.z.x.c.v.b.n.m.a1b2c3d4e5f6g7h8j9k0z.top"
    assert suspicion.score(worst).score == 100


def test_the_longest_legal_name_is_cheap_to_score() -> None:
    name = ".".join(["a" * 61] * 4)
    verdict = suspicion.score(name)
    assert 0 <= verdict.score <= 100


def test_entropy_is_zero_for_nothing_and_grows_with_variety() -> None:
    assert suspicion.entropy("") == 0.0
    assert suspicion.entropy("aaaa") == 0.0
    assert suspicion.entropy("abcd") == 2.0
