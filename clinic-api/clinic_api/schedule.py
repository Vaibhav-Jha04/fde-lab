"""Opening hours, slot generation and lenient parsing of what a voice agent sends."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

SLOT_MINUTES = 30

# weekday() -> list of (open, close) blocks. Monday is 0.
HOURS: dict[int, list[tuple[time, time]]] = {
    0: [(time(9), time(12)), (time(13), time(17))],
    1: [(time(9), time(12)), (time(13), time(17))],
    2: [(time(9), time(12)), (time(13), time(17))],
    3: [(time(9), time(12)), (time(13), time(17))],
    4: [(time(9), time(12)), (time(13), time(17))],
    5: [(time(9), time(13))],
}


def all_slots(day: date) -> list[time]:
    slots = []
    for start, end in HOURS.get(day.weekday(), []):
        t = datetime.combine(day, start)
        while t + timedelta(minutes=SLOT_MINUTES) <= datetime.combine(day, end):
            slots.append(t.time())
            t += timedelta(minutes=SLOT_MINUTES)
    return slots


def parse_date(value: str) -> date | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(value.replace(",", ""), fmt).date()
        except ValueError:
            continue
    return None


def parse_time(value: str) -> time | None:
    value = value.strip().upper().replace(".", "")
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def spoken_time(t: time) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def spoken_date(d: date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


def normalise_phone(value: str) -> str:
    digits = "".join(c for c in value if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits
