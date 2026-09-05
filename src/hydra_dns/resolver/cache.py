from __future__ import annotations

import base64
import json
import logging
import math
import os
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import dns.flags
import dns.message
import dns.rcode

log = logging.getLogger(__name__)

CacheKey = tuple[str, int, int, bool]

SNAPSHOT_VERSION = 1


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expired: int = 0
    stored: int = 0
    stale_hits: int = 0
    restored: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


@dataclass
class _Entry:
    wire: bytes
    stored_at: float
    expires_at: float
    client_ttl: int | None = None
    counted_expired: bool = False
    reads: int = 0


class AnswerCache:
    def __init__(
        self,
        max_entries: int,
        min_ttl: int,
        max_ttl: int,
        blocked_min_ttl: int | None = None,
        stale_window_s: int = 0,
    ) -> None:
        self._entries: OrderedDict[CacheKey, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._min_ttl = min_ttl
        self._max_ttl = max_ttl
        self._blocked_min_ttl = min_ttl if blocked_min_ttl is None else blocked_min_ttl
        self._stale_window_s = max(0, stale_window_s)
        self.stats = CacheStats()

    @property
    def stale_window_s(self) -> int:
        return self._stale_window_s

    @staticmethod
    def key_for(query: dns.message.Message) -> CacheKey | None:
        if len(query.question) != 1:
            return None
        question = query.question[0]
        want_dnssec = bool(query.ednsflags & dns.flags.DO)
        return (question.name.to_text().lower(), question.rdtype, question.rdclass, want_dnssec)

    def get(self, key: CacheKey, request_id: int) -> bytes | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.stats.misses += 1
                return None
            if entry.expires_at <= now:
                if not entry.counted_expired:
                    entry.counted_expired = True
                    self.stats.expired += 1
                if now > entry.expires_at + self._stale_window_s:
                    del self._entries[key]
                self.stats.misses += 1
                return None
            self._entries.move_to_end(key)
            self.stats.hits += 1
            entry.reads += 1
            wire, stored_at = entry.wire, entry.stored_at
            client_ttl, expires_at = entry.client_ttl, entry.expires_at

        if client_ttl is not None:
            remaining = int(expires_at - now)
            return _set_ttls(wire, max(1, min(client_ttl, remaining)), request_id)

        elapsed = int(now - stored_at)
        return _rewrite(wire, elapsed, request_id)

    def peek(self, key: CacheKey) -> dict:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            expires_at = entry.expires_at if entry is not None else None
        if expires_at is None:
            return {"state": "absent", "ttl": 0}
        remaining = expires_at - now
        if remaining > 0:
            return {"state": "live", "ttl": math.ceil(remaining)}
        if -remaining <= self._stale_window_s:
            return {"state": "stale", "ttl": math.floor(remaining)}
        return {"state": "absent", "ttl": 0}

    def wire_of(self, key: CacheKey) -> bytes | None:
        with self._lock:
            entry = self._entries.get(key)
            return entry.wire if entry is not None else None

    def has(self, key: CacheKey) -> bool:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            return entry is not None and entry.expires_at > now

    def get_stale(self, key: CacheKey, request_id: int) -> bytes | None:
        if self._stale_window_s <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.expires_at > now:
                return None
            if now > entry.expires_at + self._stale_window_s:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            self.stats.stale_hits += 1
            entry.reads += 1
            wire = entry.wire

        return _set_ttls(wire, self._min_ttl, request_id)

    def put(
        self,
        key: CacheKey,
        response: dns.message.Message,
        wire: bytes,
        blocked: bool = False,
    ) -> int | None:
        ttl = self.ttl_for(response, blocked)
        if ttl is None:
            return None

        client_ttl = self._min_ttl if blocked and ttl > self._min_ttl else None
        raised = _raise_ttls(response, ttl if client_ttl is None else client_ttl)
        if response.id != 0 or raised:
            response.id = 0
            wire = response.to_wire()

        now = time.monotonic()
        with self._lock:
            self._entries[key] = _Entry(
                wire=wire, stored_at=now, expires_at=now + ttl, client_ttl=client_ttl
            )
            self._entries.move_to_end(key)
            self.stats.stored += 1
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                self.stats.evictions += 1
        return ttl

    def ttl_for(self, response: dns.message.Message, blocked: bool = False) -> int | None:
        if response.flags & dns.flags.TC:
            return None
        rcode = response.rcode()
        if rcode not in (dns.rcode.NOERROR, dns.rcode.NXDOMAIN):
            return None

        floor = self._blocked_min_ttl if blocked else self._min_ttl

        if response.answer:
            ttl = min(rrset.ttl for rrset in response.answer)
        elif response.authority:
            ttl = min(rrset.ttl for rrset in response.authority)
            for rrset in response.authority:
                for rdata in rrset:
                    minimum = getattr(rdata, "minimum", None)
                    if minimum is not None:
                        ttl = min(ttl, int(minimum))
        else:
            ttl = floor

        if ttl <= 0:
            return None
        return min(self._max_ttl, max(floor, int(ttl)))

    def due(self, within: float, min_reads: int = 0) -> list[tuple[CacheKey, float]]:
        edge = time.monotonic() + within
        with self._lock:
            return [
                (key, entry.expires_at)
                for key, entry in self._entries.items()
                if entry.expires_at <= edge and entry.reads >= min_reads
            ]

    def purge_expired(self) -> int:
        now = time.monotonic()
        with self._lock:
            dead = [
                k for k, e in self._entries.items() if now > e.expires_at + self._stale_window_s
            ]
            for key in dead:
                if not self._entries[key].counted_expired:
                    self.stats.expired += 1
                del self._entries[key]
        return len(dead)

    def clear(self) -> int:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
        return count

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def snapshot(self) -> dict[str, float | int]:
        now = time.monotonic()
        with self._lock:
            entries = len(self._entries)
            stale = sum(1 for e in self._entries.values() if e.expires_at <= now)
        return {
            "entries": entries,
            "live": entries - stale,
            "stale": stale,
            "capacity": self._max_entries,
            "hits": self.stats.hits,
            "misses": self.stats.misses,
            "hit_rate": round(self.stats.hit_rate, 4),
            "evictions": self.stats.evictions,
            "expired": self.stats.expired,
            "stale_hits": self.stats.stale_hits,
            "restored": self.stats.restored,
            "stale_window_s": self._stale_window_s,
        }

    def export_entries(self, limit: int = 0) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            items = list(self._entries.items())
        if limit > 0:
            items = items[-limit:]

        rows: list[dict] = []
        for (name, rdtype, rdclass, want_dnssec), entry in items:
            remaining = entry.expires_at - now
            if remaining <= 0:
                continue
            rows.append(
                {
                    "k": [name, rdtype, rdclass, want_dnssec],
                    "w": base64.b64encode(entry.wire).decode("ascii"),
                    "r": round(remaining, 3),
                    "a": round(now - entry.stored_at, 3),
                    "c": entry.client_ttl,
                }
            )
        return rows

    def import_entries(self, rows: list[dict], aged_s: float = 0.0) -> int:
        now = time.monotonic()
        restored = 0
        with self._lock:
            for row in rows:
                try:
                    name, rdtype, rdclass, want_dnssec = row["k"]
                    remaining = float(row["r"]) - aged_s
                    if remaining <= 0:
                        continue
                    key: CacheKey = (str(name), int(rdtype), int(rdclass), bool(want_dnssec))
                    client_ttl = row.get("c")
                    self._entries[key] = _Entry(
                        wire=base64.b64decode(row["w"]),
                        stored_at=now - (float(row.get("a", 0.0)) + aged_s),
                        expires_at=now + remaining,
                        client_ttl=int(client_ttl) if client_ttl is not None else None,
                    )
                    restored += 1
                except (KeyError, ValueError, TypeError):
                    continue
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                self.stats.evictions += 1
            self.stats.restored += restored
        return restored

    def save(self, path: str, limit: int = 0) -> int:
        if not path:
            return 0
        rows = self.export_entries(limit)
        payload = {"version": SNAPSHOT_VERSION, "saved_at": time.time(), "entries": rows}
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".cache-")
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(payload, out, separators=(",", ":"))
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        except OSError as exc:
            log.warning("could not write the cache snapshot to %s: %s", path, exc)
            return 0
        return len(rows)

    def load(self, path: str) -> int:
        if not path:
            return 0
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.info("no usable cache snapshot at %s (%s), starting cold", path, exc)
            return 0

        if not isinstance(payload, dict) or payload.get("version") != SNAPSHOT_VERSION:
            log.info("ignoring a cache snapshot written by another version")
            return 0

        rows = payload.get("entries")
        if not isinstance(rows, list):
            return 0

        aged = max(0.0, time.time() - float(payload.get("saved_at") or 0))
        restored = self.import_entries(rows, aged_s=aged)
        log.info("restored %s cached answer(s) written %.0fs ago", restored, aged)
        return restored


def _raise_ttls(message: dns.message.Message, ttl: int) -> bool:
    raised = False
    for section in (message.answer, message.authority):
        for rrset in section:
            if rrset.ttl < ttl:
                rrset.ttl = ttl
                raised = True
    return raised


def _set_ttls(wire: bytes, ttl: int, request_id: int) -> bytes:
    try:
        message = dns.message.from_wire(wire)
    except Exception:
        return b""

    for section in (message.answer, message.authority):
        for rrset in section:
            rrset.ttl = ttl

    message.id = request_id
    return message.to_wire()


def _rewrite(wire: bytes, elapsed: int, request_id: int) -> bytes:
    try:
        message = dns.message.from_wire(wire)
    except Exception:
        return b""

    if elapsed > 0:
        for section in (message.answer, message.authority, message.additional):
            for rrset in section:
                rrset.ttl = max(1, rrset.ttl - elapsed)

    message.id = request_id
    return message.to_wire()
