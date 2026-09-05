from __future__ import annotations

import asyncio

import dns.message
import dns.rdatatype
import httpx
import pytest
import respx
from conftest import make_answer

from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.inflight import SingleFlight
from hydra_dns.resolver.metrics import Metrics
from hydra_dns.resolver.service import Resolver
from hydra_dns.resolver.upstream import Upstream, UpstreamError, UpstreamPool

UPSTREAM = "https://upstream.test/dns-query"


@pytest.fixture
def resolver() -> Resolver:
    client = httpx.AsyncClient()
    pool = UpstreamPool(strategy="round_robin", timeout_s=5.0)
    pool.attach_client(client)
    pool.set_upstreams([Upstream(label="one", doh_url=UPSTREAM)])
    cache = AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600, stale_window_s=600)
    return Resolver(cache, pool, Metrics())


def query(name: str = "example.com") -> bytes:
    return dns.message.make_query(name, dns.rdatatype.A).to_wire()


async def test_one_answer_is_shared_by_everyone_waiting_for_it(resolver) -> None:
    held = asyncio.Event()
    calls = 0

    async def slow(request):
        nonlocal calls
        calls += 1
        await held.wait()
        return httpx.Response(200, content=make_answer()[1])

    with respx.mock:
        respx.post(UPSTREAM).mock(side_effect=slow)
        waiting = [asyncio.create_task(resolver.resolve(query(), "d")) for _ in range(5)]
        await asyncio.sleep(0.05)
        held.set()
        results = await asyncio.gather(*waiting)

    assert calls == 1
    assert resolver.flight.led == 1
    assert resolver.flight.shared == 4
    assert all(r.rcode == "NOERROR" for r in results)
    assert sum(1 for r in results if r.shared) == 4


async def test_each_waiter_gets_its_own_message_id(resolver) -> None:
    held = asyncio.Event()

    async def slow(request):
        await held.wait()
        return httpx.Response(200, content=make_answer()[1])

    async def ask(message_id: int):
        message = dns.message.make_query("example.com", dns.rdatatype.A)
        message.id = message_id
        return await resolver.resolve(message.to_wire(), "d")

    with respx.mock:
        respx.post(UPSTREAM).mock(side_effect=slow)
        waiting = [asyncio.create_task(ask(n)) for n in (11, 22, 33)]
        await asyncio.sleep(0.05)
        held.set()
        results = await asyncio.gather(*waiting)

    assert [dns.message.from_wire(r.wire).id for r in results] == [11, 22, 33]


async def test_different_names_are_not_folded_together(resolver) -> None:
    with respx.mock:
        route = respx.post(UPSTREAM).mock(
            return_value=httpx.Response(200, content=make_answer()[1])
        )
        await asyncio.gather(
            resolver.resolve(query("one.test"), "d"),
            resolver.resolve(query("two.test"), "d"),
        )

    assert route.call_count == 2
    assert resolver.flight.shared == 0


async def test_only_the_leader_writes_to_the_cache(resolver) -> None:
    held = asyncio.Event()

    async def slow(request):
        await held.wait()
        return httpx.Response(200, content=make_answer()[1])

    with respx.mock:
        respx.post(UPSTREAM).mock(side_effect=slow)
        waiting = [asyncio.create_task(resolver.resolve(query(), "d")) for _ in range(4)]
        await asyncio.sleep(0.05)
        held.set()
        await asyncio.gather(*waiting)

    assert resolver._cache.stats.stored == 1


async def test_a_shared_failure_reaches_every_waiter(resolver) -> None:
    held = asyncio.Event()

    async def slow(request):
        await held.wait()
        raise httpx.ConnectError("upstream is gone")

    with respx.mock:
        respx.post(UPSTREAM).mock(side_effect=slow)
        waiting = [asyncio.create_task(resolver.resolve(query(), "d")) for _ in range(3)]
        await asyncio.sleep(0.05)
        held.set()
        results = await asyncio.gather(*waiting)

    assert all(r.rcode == "SERVFAIL" for r in results)


async def test_the_flight_is_empty_once_everyone_has_been_answered(resolver) -> None:
    with respx.mock:
        respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))
        await resolver.resolve(query(), "d")

    assert resolver.flight.in_flight == 0


async def test_a_second_wave_leads_again_rather_than_waiting_forever(resolver) -> None:
    with respx.mock:
        route = respx.post(UPSTREAM).mock(
            return_value=httpx.Response(200, content=make_answer()[1])
        )
        await resolver.resolve(query(), "d")
        resolver._cache.clear()
        await resolver.resolve(query(), "d")

    assert route.call_count == 2
    assert resolver.flight.led == 2


async def test_single_flight_returns_the_leader_result_to_a_follower() -> None:
    flight = SingleFlight()
    held = asyncio.Event()

    async def slow():
        await held.wait()
        return "answer"

    leader = asyncio.create_task(flight.run("k", slow))
    await asyncio.sleep(0.01)
    follower = asyncio.create_task(flight.run("k", slow))
    await asyncio.sleep(0.01)
    held.set()

    assert await leader == ("answer", False)
    assert await follower == ("answer", True)


async def test_a_cancelled_follower_does_not_take_the_leader_down() -> None:
    flight = SingleFlight()
    held = asyncio.Event()

    async def slow():
        await held.wait()
        return "answer"

    leader = asyncio.create_task(flight.run("k", slow))
    await asyncio.sleep(0.01)
    follower = asyncio.create_task(flight.run("k", slow))
    await asyncio.sleep(0.01)

    follower.cancel()
    with pytest.raises(asyncio.CancelledError):
        await follower

    held.set()
    assert await leader == ("answer", False)


async def test_a_leader_failure_is_not_remembered() -> None:
    flight = SingleFlight()

    async def boom():
        raise UpstreamError("no")

    with pytest.raises(UpstreamError):
        await flight.run("k", boom)

    assert flight.in_flight == 0

    async def fine():
        return "ok"

    assert await flight.run("k", fine) == ("ok", False)
