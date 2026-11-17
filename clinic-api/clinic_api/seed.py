"""Fill the database with a few made-up bookings so some slots are already taken.

Run: uv run python -m clinic_api.seed
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import schedule
from .db import Database

PATIENTS = [
    ("Priya Shah", "555-010-2001", "cleaning"),
    ("Tom Becker", "555-010-2002", "toothache"),
    ("Ana Lopez", "555-010-2003", "check-up"),
    ("Sam Okafor", "555-010-2004", "filling"),
]


def main() -> None:
    db = Database(os.environ.get("CLINIC_DB", "clinic.db"))
    tz = ZoneInfo(os.environ.get("CLINIC_TZ", "America/New_York"))
    today = datetime.now(tz).date()
    added = 0
    with db.connect() as conn:
        for offset in range(1, 6):
            day = today + timedelta(days=offset)
            slots = schedule.all_slots(day)
            for i, (name, phone, reason) in enumerate(PATIENTS):
                if i * 3 >= len(slots):
                    break
                starts_at = f"{day.isoformat()}T{slots[i * 3].strftime('%H:%M')}"
                cur = conn.execute(
                    "INSERT OR IGNORE INTO appointments (patient_name, phone, reason, starts_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, schedule.normalise_phone(phone), reason, starts_at, datetime.now(tz).isoformat(timespec="seconds")),
                )
                added += cur.rowcount
    print(f"added {added} appointments")


if __name__ == "__main__":
    main()
