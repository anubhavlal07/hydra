from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import settings

CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def zone() -> dt.tzinfo:
    name = (settings.timezone or "").strip()
    if not name:
        return dt.UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return dt.UTC


def now() -> dt.datetime:
    return dt.datetime.now(zone())


def minutes(clock: str) -> int | None:
    match = CLOCK.match((clock or "").strip())
    if match is None:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def normalise_days(values: object) -> tuple[int, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    seen: list[int] = []
    for raw in values:
        try:
            day = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6 and day not in seen:
            seen.append(day)
    return tuple(sorted(seen))


@dataclass(frozen=True)
class Window:
    days: tuple[int, ...] = ()
    starts_at: str = ""
    ends_at: str = ""

    @property
    def all_day(self) -> bool:
        return minutes(self.starts_at) is None or minutes(self.ends_at) is None

    @property
    def wraps(self) -> bool:
        start, end = minutes(self.starts_at), minutes(self.ends_at)
        return start is not None and end is not None and end < start

    def _day_holds(self, weekday: int) -> bool:
        return not self.days or weekday in self.days

    def covers(self, moment: dt.datetime) -> bool:
        start, end = minutes(self.starts_at), minutes(self.ends_at)
        if start is None or end is None:
            return self._day_holds(moment.weekday())
        if start == end:
            return False

        minute = moment.hour * 60 + moment.minute

        if start < end:
            return self._day_holds(moment.weekday()) and start <= minute < end

        if minute >= start:
            return self._day_holds(moment.weekday())
        if minute < end:
            return self._day_holds((moment.weekday() - 1) % 7)
        return False

    def describe(self) -> str:
        when = "every day" if not self.days else " ".join(DAYS[d] for d in self.days)
        if self.all_day:
            return when
        return f"{self.starts_at}–{self.ends_at}, {when}"


def describe_span(seconds: float) -> str:
    if seconds < 3600:
        minutes = max(1, int(seconds // 60))
        return "a minute" if minutes == 1 else f"{minutes} minutes"
    if seconds < 86_400:
        hours = int(seconds // 3600)
        return "an hour" if hours == 1 else f"{hours} hours"
    days = int(seconds // 86_400)
    return "a day" if days == 1 else f"{days} days"
