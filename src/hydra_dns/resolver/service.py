from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import dataclass

import dns.edns
import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset

from .. import designated, localzones, rebind, safesearch, uncloak
from ..config import settings
from ..decision import (
    APP,
    BLOCKLIST,
    DESIGNATED,
    DESIGNATED_DECISION,
    DEVICE_RULE,
    DEVICE_RULE_DECISION,
    LOCAL_PTR,
    LOCAL_ZONE,
    PAUSED,
    PAUSED_DECISION,
    REWRITE,
    SAFE_SEARCH,
    SCHEDULE,
    Decision,
)
from . import savings as saved
from .budget import DeviceBudget
from .cache import AnswerCache
from .inflight import SingleFlight
from .metrics import Metrics, QueryRecord
from .upstream import UpstreamError, UpstreamPool

log = logging.getLogger(__name__)

BLOCK_SENTINELS = settings.block_sentinels
BLOCK_SOA_NAMES = settings.block_soa_names
LOCAL_ZONES = localzones.Zones(settings.local_zones)
LOCAL_PTR_INDEX = localzones.ptr_index(settings.rewrites)
_LOOK_UP = object()
REBIND_GUARD = rebind.LABEL


@dataclass
class ResolveResult:
    wire: bytes
    name: str
    qtype: str
    rcode: str
    cached: bool
    blocked: bool
    upstream: str
    latency_ms: float
    ttl: int | None = None
    stale: bool = False
    shared: bool = False


class Resolver:
    def __init__(
        self,
        cache: AnswerCache,
        pool: UpstreamPool,
        metrics: Metrics,
        devices: object | None = None,
        budget: DeviceBudget | None = None,
        blocklists: object | None = None,
        guard: rebind.RebindGuard | None = None,
        apps: object | None = None,
        uncloaker: uncloak.Uncloaker | None = None,
    ) -> None:
        self._cache = cache
        self._pool = pool
        self._metrics = metrics
        self._devices = devices
        self._pauses = getattr(devices, "pauses", None)
        self._blocklists = blocklists
        self._apps = apps
        self.rebind = guard if guard is not None else rebind.build(settings)
        self.uncloak = uncloaker if uncloaker is not None else uncloak.build(settings)
        self.budget = budget or DeviceBudget(settings.device_hourly_budget)
        self.flight = SingleFlight()

    def budget_snapshot(self) -> dict:
        known = getattr(self._devices, "devices", None)
        if isinstance(known, list):
            self.budget.set_ceilings({d.id: d.hourly_budget for d in known})
        return self.budget.snapshot()

    async def resolve(self, wire: bytes, device: str) -> ResolveResult:
        started = time.perf_counter()

        try:
            query = dns.message.from_wire(wire)
        except Exception as exc:
            self._metrics.record_refused()
            raise MalformedQuery(str(exc)) from exc

        if not query.question:
            self._metrics.record_refused()
            raise MalformedQuery("query carries no question")

        question = query.question[0]
        name = question.name.to_text(omit_final_dot=True).lower() or "."
        qtype = dns.rdatatype.to_text(question.rdtype)

        if question.rdtype == dns.rdatatype.ANY:
            return self._immediate(query, name, qtype, dns.rcode.REFUSED, started, device)

        known = self._devices.get(device) if hasattr(self._devices, "get") else None
        decision = self.decide(name, qtype, device, known=known)
        if decision is not None:
            answered = await self._answer(decision, query, name, qtype, started, device, known)
            if answered is not None:
                return answered

        key = self._cache.key_for(query)
        if key is not None:
            hit = self._cache.get(key, query.id)
            if hit:
                latency_ms = (time.perf_counter() - started) * 1000
                response = dns.message.from_wire(hit)
                blocked = _looks_blocked(response)
                self._metrics.savings.note(saved.CACHE)
                if not blocked and self.rebind.refuses(name, response):
                    return await self._guarded(query, name, qtype, started, device, known, True)
                cloaked = None if blocked else self._cloaked(device, name, response)
                if cloaked is not None:
                    return await self._uncloaked(
                        cloaked, query, name, qtype, started, device, known, True
                    )
                result = ResolveResult(
                    wire=hit,
                    name=name,
                    qtype=qtype,
                    rcode=dns.rcode.to_text(response.rcode()),
                    cached=True,
                    blocked=blocked,
                    upstream="cache",
                    latency_ms=latency_ms,
                )
                self._remember(result, device)
                return result

        original_id = query.id

        if not self.budget.allow(device, known.hourly_budget if known is not None else None):
            spared = await self._from_stale(
                key, query, name, qtype, started, device, known, "budget"
            )
            if spared is not None:
                return spared
            log.warning("device %s is over its hourly budget; refusing %s %s", device, name, qtype)
            return self._fail(query, name, qtype, started, device, upstream="device budget")

        query.id = 0
        wire_out = query.to_wire()

        async def forward():
            return await self._pool.forward(wire_out)

        try:
            if key is None:
                answer_wire, upstream, _ = await forward()
                shared = False
            else:
                (answer_wire, upstream, _), shared = await self.flight.run(key, forward)
        except UpstreamError as exc:
            log.warning("all upstreams failed for %s %s: %s", name, qtype, exc)
            query.id = original_id
            spared = await self._from_stale(
                key, query, name, qtype, started, device, known, "upstream"
            )
            if spared is not None:
                return spared
            return self._fail(query, name, qtype, started, device)

        try:
            response = dns.message.from_wire(answer_wire)
        except Exception as exc:
            log.warning("upstream %s returned unparseable bytes: %s", upstream.label, exc)
            query.id = original_id
            return self._fail(query, name, qtype, started, device)

        blocked = _looks_blocked(response)
        if shared and not upstream.is_fallback:
            self._metrics.savings.note(saved.COALESCED)
        rebound = not blocked and self.rebind.refuses(name, response)

        ttl = None
        if key is not None and not upstream.is_fallback:
            held = blocked or rebound
            if rebound:
                _hold_briefly(response)
            ttl = (
                self._cache.ttl_for(response, held)
                if shared
                else self._cache.put(key, response, answer_wire, held)
            )

        if rebound:
            query.id = original_id
            return await self._guarded(query, name, qtype, started, device, known)

        cloaked = None if blocked else self._cloaked(device, name, response)
        if cloaked is not None:
            query.id = original_id
            return await self._uncloaked(cloaked, query, name, qtype, started, device, known)

        response.id = original_id
        latency_ms = (time.perf_counter() - started) * 1000
        result = ResolveResult(
            wire=response.to_wire(),
            name=name,
            qtype=qtype,
            rcode=dns.rcode.to_text(response.rcode()),
            cached=False,
            blocked=blocked,
            upstream=upstream.label,
            latency_ms=latency_ms,
            ttl=ttl,
            shared=shared,
        )
        self._remember(result, device)
        return result

    def decide(
        self, name: str, qtype: str, device: str, *, known: object = _LOOK_UP
    ) -> Decision | None:
        store = self._devices
        lookup = getattr(store, "rewrite_for", None)
        rewrite = lookup(name) if lookup else settings.rewrites.get(name)
        if rewrite is not None and qtype in ("A", "AAAA"):
            return Decision(layer=REWRITE, label=REWRITE, detail=rewrite)

        if qtype == "SVCB" and name == designated.NAME:
            return DESIGNATED_DECISION

        if qtype == "PTR":
            hosts = self._ptr_for(name)
            if hosts:
                return Decision(layer=LOCAL_PTR, label=LOCAL_PTR, detail=hosts)

        verdict = LOCAL_ZONES.verdict_for(name, qtype)
        if verdict is not None:
            return Decision(layer=LOCAL_ZONE, label=LOCAL_ZONE, detail=verdict)

        if self._pauses is not None and self._pauses.active(device):
            return PAUSED_DECISION

        if known is _LOOK_UP:
            known = store.get(device) if hasattr(store, "get") else None

        ruled = self._ruled(device, name)
        if ruled is not None and ruled.blocked:
            return ruled

        if ruled is None:
            app = self._app_for(device, name)
            if app is not None:
                return Decision(layer=APP, label=f"app {app.name}", blocked=True, source=app.id)
            listed = self._match(device, name)
            if listed is not None:
                label = f"blocklist {listed.name}"
                return Decision(layer=BLOCKLIST, label=label, blocked=True, source=listed.id)

        if known is not None and known.safe_search and qtype in ("A", "AAAA"):
            target = safesearch.target_for(name, getattr(known, "safe_search_skip", ()))
            if target is not None:
                return Decision(layer=SAFE_SEARCH, label=SAFE_SEARCH, detail=target)

        return ruled

    def _ruled(self, device: str, name: str) -> Decision | None:
        explain = getattr(self._devices, "explain", None)
        if explain is not None:
            return explain(device, name)
        blocker = getattr(self._devices, "blocks", None)
        if blocker is not None and blocker(device, name):
            return DEVICE_RULE_DECISION
        return None

    def _match(self, device: str, name: str):
        lists = self._blocklists
        if lists is None:
            return None
        try:
            return lists.match(device, name)
        except Exception as exc:
            log.debug("blocklist check failed for %s, answering as unlisted: %s", name, exc)
            return None

    def _app_for(self, device: str, name: str):
        apps = self._apps
        if apps is None:
            return None
        try:
            return apps.match(device, name)
        except Exception as exc:
            log.debug("app check failed for %s, answering as unblocked: %s", name, exc)
            return None

    def _cloaked(self, device: str, name: str, response: dns.message.Message) -> Decision | None:
        if not self.uncloak.enabled or (self._blocklists is None and self._apps is None):
            return None
        try:
            if not response.answer:
                return None
            for target in uncloak.targets(response, name):
                found = self._cloaked_by(device, name, target)
                if found is None:
                    continue
                own = self._ruled(device, target)
                if own is not None and not own.blocked:
                    continue
                asked = self._ruled(device, name)
                if asked is not None and not asked.blocked:
                    return None
                return found
        except Exception as exc:
            self.uncloak.failed += 1
            log.debug("cname check failed for %s, passing the answer through: %s", name, exc)
        return None

    def _cloaked_by(self, device: str, name: str, target: str) -> Decision | None:
        app = self._app_for(device, target)
        if app is not None:
            return Decision(
                layer=APP,
                label=uncloak.label_for(target, f"app {app.name}"),
                blocked=True,
                source=app.id,
                rule=target,
            )
        lists = self._blocklists
        if lists is None:
            return None
        cloaking = getattr(lists, "cloaking", None)
        listed = cloaking(device, name, target) if cloaking else lists.match(device, target)
        if listed is None:
            return None
        return Decision(
            layer=BLOCKLIST,
            label=uncloak.label_for(target, f"blocklist {listed.name}"),
            blocked=True,
            source=listed.id,
            rule=target,
        )

    async def _uncloaked(
        self,
        decision: Decision,
        query: dns.message.Message,
        name: str,
        qtype: str,
        started: float,
        device: str,
        known: object | None,
        cached: bool = False,
    ) -> ResolveResult:
        self.uncloak.note()
        if decision.layer == APP:
            self._count_app(decision.source)
        else:
            self._count_hit(decision.source)
        return await self._refuse(
            query, name, qtype, started, device, known, label=decision.label, cached=cached
        )

    def _count_hit(self, list_id: str | None) -> None:
        try:
            self._blocklists.hit(list_id)  # type: ignore[union-attr]
        except Exception as exc:
            log.debug("could not count a blocklist hit on %s: %s", list_id, exc)

    def _count_app(self, app_id: str | None) -> None:
        try:
            self._apps.hit(app_id)  # type: ignore[union-attr]
        except Exception as exc:
            log.debug("could not count an app hit on %s: %s", app_id, exc)

    async def _answer(
        self,
        decision: Decision,
        query: dns.message.Message,
        name: str,
        qtype: str,
        started: float,
        device: str,
        known: object | None,
    ) -> ResolveResult | None:
        layer = decision.layer
        if layer == REWRITE:
            return self._local(query, name, qtype, started, device, decision.detail)
        if layer == DESIGNATED:
            return self._designated(query, name, qtype, started, device)
        if layer == LOCAL_PTR:
            return self._local_ptr(query, name, qtype, started, device, decision.detail)
        if layer == LOCAL_ZONE:
            return self._local_zone(query, name, qtype, started, device, decision.detail)
        if layer == PAUSED:
            return await self._unfiltered(query, name, qtype, started, device, known)
        if layer in (DEVICE_RULE, SCHEDULE):
            self._metrics.savings.note(saved.RULE)
            return await self._refuse(query, name, qtype, started, device, known)
        if layer == APP:
            self._count_app(decision.source)
            self._metrics.savings.note(saved.APP)
            return await self._refuse(
                query, name, qtype, started, device, known, label=decision.label
            )
        if layer == BLOCKLIST:
            self._count_hit(decision.source)
            self._metrics.savings.note(saved.BLOCKLIST)
            return await self._refuse(
                query, name, qtype, started, device, known, label=decision.label
            )
        if layer == SAFE_SEARCH:
            return await self._stand_in(query, name, qtype, started, device, decision.detail)
        return None

    async def _unfiltered(
        self,
        query: dns.message.Message,
        name: str,
        qtype: str,
        started: float,
        device: str,
        known: object | None,
    ) -> ResolveResult:
        if not self.budget.allow(device, getattr(known, "hourly_budget", None)):
            log.warning("device %s is over its hourly budget; refusing %s %s", device, name, qtype)
            return self._fail(query, name, qtype, started, device, upstream="device budget")

        original_id = query.id
        query.id = 0
        try:
            answer_wire, upstream, _ = await self._pool.forward_unfiltered(query.to_wire())
            response = dns.message.from_wire(answer_wire)
        except UpstreamError as exc:
            log.warning("no unfiltered answer for %s %s: %s", name, qtype, exc)
            query.id = original_id
            return self._fail(query, name, qtype, started, device)
        except Exception as exc:
            log.warning("unfiltered upstream returned unparseable bytes: %s", exc)
            query.id = original_id
            return self._fail(query, name, qtype, started, device)

        blocked = _looks_blocked(response)
        if not blocked and self.rebind.refuses(name, response):
            query.id = original_id
            return await self._guarded(query, name, qtype, started, device, known)

        response.id = original_id
        result = ResolveResult(
            wire=response.to_wire(),
            name=name,
            qtype=qtype,
            rcode=dns.rcode.to_text(response.rcode()),
            cached=False,
            blocked=blocked,
            upstream=f"paused → {upstream.label}",
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        self._remember(result, device)
        return result

    async def _from_stale(
        self,
        key,
        query: dns.message.Message,
        name: str,
        qtype: str,
        started: float,
        device: str,
        known: object | None,
        why: str,
    ) -> ResolveResult | None:
        if key is None:
            return None
        wire = self._cache.get_stale(key, query.id)
        if not wire:
            return None

        response = dns.message.from_wire(wire)
        blocked = _looks_blocked(response)
        if not blocked and self.rebind.refuses(name, response):
            return await self._guarded(query, name, qtype, started, device, known, True)
        cloaked = None if blocked else self._cloaked(device, name, response)
        if cloaked is not None:
            return await self._uncloaked(cloaked, query, name, qtype, started, device, known, True)
        result = ResolveResult(
            wire=wire,
            name=name,
            qtype=qtype,
            rcode=dns.rcode.to_text(response.rcode()),
            cached=True,
            blocked=blocked,
            upstream=f"stale ({why})",
            latency_ms=(time.perf_counter() - started) * 1000,
            ttl=settings.cache_min_ttl_s,
            stale=True,
        )
        self._remember(result, device)
        return result

    def _immediate(
        self,
        query: dns.message.Message,
        name: str,
        qtype: str,
        rcode: int,
        started: float,
        device: str,
    ) -> ResolveResult:
        response = dns.message.make_response(query)
        response.set_rcode(rcode)
        result = ResolveResult(
            wire=response.to_wire(),
            name=name,
            qtype=qtype,
            rcode=dns.rcode.to_text(rcode),
            cached=False,
            blocked=False,
            upstream="local",
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        self._remember(result, device)
        return result

    def _fail(
        self,
        query: dns.message.Message,
        name: str,
        qtype: str,
        started: float,
        device: str,
        upstream: str = "none",
    ) -> ResolveResult:
        result = self._servfail(query, name, qtype, started)
        result.upstream = upstream
        self._remember(result, device, error=True)
        return result

    async def _stand_in(
        self,
        query: dns.message.Message,
        name: str,
        qtype: str,
        started: float,
        device: str,
        target: str,
    ) -> ResolveResult | None:
        addresses = await self._addresses_of(target, qtype)
        if not addresses:
            return None

        response = dns.message.make_response(query)
        response.answer.append(
            dns.rrset.from_text_list(
                query.question[0].name, settings.safe_search_ttl_s, "IN", qtype, addresses
            )
        )

        result = ResolveResult(
            wire=response.to_wire(),
            name=name,
            qtype=qtype,
            rcode="NOERROR",
            cached=False,
            blocked=False,
            upstream="safe search",
            latency_ms=(time.perf_counter() - started) * 1000,
            ttl=settings.safe_search_ttl_s,
        )
        self._remember(result, device)
        return result

    async def _addresses_of(self, target: str, qtype: str) -> list[str]:
        probe = dns.message.make_query(target, dns.rdatatype.from_text(qtype))
        key = self._cache.key_for(probe)

        if key is not None:
            hit = self._cache.get(key, 0)
            if hit:
                return _addresses(dns.message.from_wire(hit), qtype)

        probe.id = 0
        wire_out = probe.to_wire()

        async def forward():
            return await self._pool.forward(wire_out)

        try:
            if key is None:
                wire, upstream, _ = await forward()
                shared = False
            else:
                (wire, upstream, _), shared = await self.flight.run(key, forward)
            answer = dns.message.from_wire(wire)
        except (UpstreamError, Exception):
            log.warning("could not resolve the safe-search host %s", target)
            return []

        if key is not None and not shared and not upstream.is_fallback:
            self._cache.put(key, answer, wire)
        return _addresses(answer, qtype)

    async def _refuse(
        self,
        query: dns.message.Message,
        name: str,
        qtype: str,
        started: float,
        device: str,
        known: object | None = None,
        label: str = "device rule",
        cached: bool = False,
    ) -> ResolveResult:
        mode = getattr(known, "block_mode", "zero") or "zero"
        where = (getattr(known, "block_address", "") or "").strip()

        response = dns.message.make_response(query)
        answered = ""

        if mode == "nxdomain":
            response.set_rcode(dns.rcode.NXDOMAIN)
            answered = "nxdomain"
        elif mode == "address" and where and qtype in ("A", "AAAA"):
            addresses = [where] if _is_literal(where) else await self._addresses_of(where, qtype)
            wanted = [a for a in addresses if (":" in a) == (qtype == "AAAA")]
            if wanted:
                response.answer.append(
                    dns.rrset.from_text_list(
                        query.question[0].name, settings.cache_min_ttl_s, "IN", qtype, wanted
                    )
                )
                answered = where
        else:
            sentinel = {"A": "0.0.0.0", "AAAA": "::"}.get(qtype)
            if sentinel is not None:
                response.answer.append(
                    dns.rrset.from_text(
                        query.question[0].name, settings.cache_min_ttl_s, "IN", qtype, sentinel
                    )
                )
                answered = sentinel

        result = ResolveResult(
            wire=response.to_wire(),
            name=name,
            qtype=qtype,
            rcode=dns.rcode.to_text(response.rcode()),
            cached=cached,
            blocked=True,
            upstream=f"{label} → {answered}" if answered else label,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        self._remember(result, device)
        return result

    async def _guarded(
        self,
        query: dns.message.Message,
        name: str,
        qtype: str,
        started: float,
        device: str,
        known: object | None,
        cached: bool = False,
    ) -> ResolveResult:
        self.rebind.note()
        return await self._refuse(
            query, name, qtype, started, device, known, label=REBIND_GUARD, cached=cached
        )

    def _ptr_for(self, name: str) -> tuple[str, ...]:
        lookup = getattr(self._devices, "ptr_for", None)
        return lookup(name) if lookup else LOCAL_PTR_INDEX.get(name, ())

    def _designated(
        self,
        query: dns.message.Message,
        name: str,
        qtype: str,
        started: float,
        device: str,
    ) -> ResolveResult:
        ttl = settings.local_zone_ttl_s
        records = designated.records_for(device)
        response = dns.message.make_response(query)

        if records:
            response.answer.append(
                dns.rrset.from_text_list(query.question[0].name, ttl, "IN", "SVCB", records)
            )
        else:
            response.authority.append(
                dns.rrset.from_text(
                    f"{designated.NAME}.",
                    ttl,
                    "IN",
                    "SOA",
                    f"localhost. nobody.invalid. 1 3600 900 604800 {ttl}",
                )
            )

        result = ResolveResult(
            wire=response.to_wire(),
            name=name,
            qtype=qtype,
            rcode="NOERROR",
            cached=False,
            blocked=False,
            upstream="designated",
            latency_ms=(time.perf_counter() - started) * 1000,
            ttl=ttl,
        )
        self._remember(result, device)
        return result

    def _local_ptr(
        self,
        query: dns.message.Message,
        name: str,
        qtype: str,
        started: float,
        device: str,
        hosts: tuple[str, ...],
    ) -> ResolveResult:
        ttl = settings.rewrite_ttl_s
        response = dns.message.make_response(query)
        response.answer.append(
            dns.rrset.from_text_list(
                query.question[0].name, ttl, "IN", "PTR", [f"{h}." for h in hosts]
            )
        )

        result = ResolveResult(
            wire=response.to_wire(),
            name=name,
            qtype=qtype,
            rcode="NOERROR",
            cached=False,
            blocked=False,
            upstream="rewrite",
            latency_ms=(time.perf_counter() - started) * 1000,
            ttl=ttl,
        )
        self._remember(result, device)
        return result

    def _local_zone(
        self,
        query: dns.message.Message,
        name: str,
        qtype: str,
        started: float,
        device: str,
        verdict: localzones.Verdict,
    ) -> ResolveResult:
        ttl = settings.local_zone_ttl_s
        response = dns.message.make_response(query)

        if verdict.address:
            response.answer.append(
                dns.rrset.from_text(query.question[0].name, ttl, "IN", qtype, verdict.address)
            )
        else:
            if not verdict.exists:
                response.set_rcode(dns.rcode.NXDOMAIN)
            response.authority.append(
                dns.rrset.from_text(
                    f"{verdict.zone}.",
                    ttl,
                    "IN",
                    "SOA",
                    f"localhost. nobody.invalid. 1 3600 900 604800 {ttl}",
                )
            )

        result = ResolveResult(
            wire=response.to_wire(),
            name=name,
            qtype=qtype,
            rcode=dns.rcode.to_text(response.rcode()),
            cached=False,
            blocked=False,
            upstream=f"local zone → {verdict.address}" if verdict.address else "local zone",
            latency_ms=(time.perf_counter() - started) * 1000,
            ttl=ttl,
        )
        self._remember(result, device)
        return result

    def _local(
        self,
        query: dns.message.Message,
        name: str,
        qtype: str,
        started: float,
        device: str,
        address: str,
    ) -> ResolveResult:
        response = dns.message.make_response(query)
        wanted = "AAAA" if ":" in address else "A"
        if wanted == qtype:
            response.answer.append(
                dns.rrset.from_text(
                    query.question[0].name, settings.rewrite_ttl_s, "IN", qtype, address
                )
            )

        result = ResolveResult(
            wire=response.to_wire(),
            name=name,
            qtype=qtype,
            rcode="NOERROR",
            cached=False,
            blocked=False,
            upstream="rewrite",
            latency_ms=(time.perf_counter() - started) * 1000,
            ttl=settings.rewrite_ttl_s,
        )
        self._remember(result, device)
        return result

    def _servfail(
        self, query: dns.message.Message, name: str, qtype: str, started: float
    ) -> ResolveResult:
        response = dns.message.make_response(query)
        response.set_rcode(dns.rcode.SERVFAIL)
        return ResolveResult(
            wire=response.to_wire(),
            name=name,
            qtype=qtype,
            rcode="SERVFAIL",
            cached=False,
            blocked=False,
            upstream="none",
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _remember(self, result: ResolveResult, device: str, error: bool = False) -> None:
        at = time.time()
        if self._apps is not None:
            try:
                self._apps.note(device, result.name, at)  # type: ignore[attr-defined]
            except Exception as exc:
                log.debug("could not note %s as seen: %s", result.name, exc)
        self._metrics.record(
            QueryRecord(
                at=at,
                device=device,
                name=result.name,
                qtype=result.qtype,
                rcode=result.rcode,
                cached=result.cached,
                blocked=result.blocked,
                upstream=result.upstream,
                latency_ms=result.latency_ms,
                error=error,
            )
        )


class MalformedQuery(ValueError):
    pass


def _addresses(message: dns.message.Message, qtype: str) -> list[str]:
    wanted = dns.rdatatype.from_text(qtype)
    return [
        rdata.to_text() for rrset in message.answer if rrset.rdtype == wanted for rdata in rrset
    ]


def _hold_briefly(response: dns.message.Message) -> None:
    for rrset in response.answer:
        if rrset.ttl < 1:
            rrset.ttl = 1


def _is_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _looks_blocked(response: dns.message.Message) -> bool:
    if response.rcode() == dns.rcode.REFUSED:
        return True
    for rrset in response.answer:
        if rrset.rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
            for rdata in rrset:
                if is_sentinel(rdata.to_text()):
                    return True
    if response.answer:
        return False
    return any(
        rdata.mname.to_text().rstrip(".").lower() in BLOCK_SOA_NAMES
        for rrset in response.authority
        if rrset.rdtype == dns.rdatatype.SOA
        for rdata in rrset
    )


def is_sentinel(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address) in BLOCK_SENTINELS


def build_query(name: str, qtype: str, want_dnssec: bool = False) -> bytes:
    message = dns.message.make_query(name, dns.rdatatype.from_text(qtype), want_dnssec=want_dnssec)
    return message.to_wire()


def answer_to_json(wire: bytes) -> dict:
    message = dns.message.from_wire(wire)

    def section(rrsets) -> list[dict]:
        out = []
        for rrset in rrsets:
            for rdata in rrset:
                out.append(
                    {
                        "name": rrset.name.to_text(),
                        "type": rrset.rdtype,
                        "type_name": dns.rdatatype.to_text(rrset.rdtype),
                        "TTL": rrset.ttl,
                        "data": rdata.to_text(),
                    }
                )
        return out

    return {
        "Status": message.rcode(),
        "StatusName": dns.rcode.to_text(message.rcode()),
        "TC": bool(message.flags & dns.flags.TC),
        "RD": bool(message.flags & dns.flags.RD),
        "RA": bool(message.flags & dns.flags.RA),
        "AD": bool(message.flags & dns.flags.AD),
        "CD": bool(message.flags & dns.flags.CD),
        "Question": [
            {
                "name": q.name.to_text(),
                "type": q.rdtype,
                "type_name": dns.rdatatype.to_text(q.rdtype),
            }
            for q in message.question
        ],
        "Answer": section(message.answer),
        "Authority": section(message.authority),
    }
