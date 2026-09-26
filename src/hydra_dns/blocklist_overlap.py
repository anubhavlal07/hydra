from __future__ import annotations

import itertools
from collections.abc import Mapping


def shared(one: frozenset[str], other: frozenset[str]) -> int:
    small, large = (one, other) if len(one) <= len(other) else (other, one)
    return sum(1 for name in small if name in large)


def unique(names: frozenset[str], others: list[frozenset[str]]) -> int:
    left = iter(names)
    for other in sorted(others, key=len, reverse=True):
        left = itertools.filterfalse(other.__contains__, left)
    return sum(1 for _ in left)


def measure(held: Mapping[str, frozenset[str]], enabled: frozenset[str]) -> dict[str, dict]:
    ids = list(held)
    pairs: dict[str, list[dict]] = {list_id: [] for list_id in ids}
    for first, second in itertools.combinations(ids, 2):
        count = shared(held[first], held[second])
        pairs[first].append({"id": second, "shared": count})
        pairs[second].append({"id": first, "shared": count})

    found: dict[str, dict] = {}
    for list_id in ids:
        others = [held[i] for i in ids if i != list_id and i in enabled]
        found[list_id] = {
            "overlap": sorted(pairs[list_id], key=lambda entry: (-entry["shared"], entry["id"])),
            "unique": unique(held[list_id], others),
        }
    return found
