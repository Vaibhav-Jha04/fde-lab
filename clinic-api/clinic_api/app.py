"""Booking API for a made-up dental clinic, built to be called by a Bland voice agent.

Business refusals (slot taken, clinic closed, bad date) come back as HTTP 200 with
`booked: false` and a sentence the agent can say. Bland only passes `ok` and `message`
to the agent when a request fails, so a 4xx would hide the reason and the alternatives.
4xx is kept for auth failures and malformed requests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import schedule
from .db import Database


@dataclass
class Settings:
    api_key: str
    webhook_secret: str | None
    db_path: str
    timezone: str = "America/New_York"
    clinic_name: str = "Maple Street Dental"

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.environ.get("CLINIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("CLINIC_API_KEY is not set")
        return cls(
            api_key=api_key,
            webhook_secret=os.environ.get("BLAND_WEBHOOK_SECRET") or None,
            db_path=os.environ.get("CLINIC_DB", "clinic.db"),
            timezone=os.environ.get("CLINIC_TZ", "America/New_York"),
        )


class BookRequest(BaseModel):
    patient_name: str = Field(min_length=1)
    phone: str = Field(min_length=7)
    reason: str = Field(min_length=1)
    date: str
    time: str


class RescheduleRequest(BaseModel):
    phone: str
    date: str
    time: str


class CancelRequest(BaseModel):
    phone: str


def create_app(settings: Settings | None = None, clock: Callable[[], datetime] | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    tz = ZoneInfo(settings.timezone)
    now = clock or (lambda: datetime.now(tz))
    db = Database(settings.db_path)
    app = FastAPI(title=f"{settings.clinic_name} booking API")

    def require_key(x_clinic_key: str = Header(default="")) -> None:
        if not hmac.compare_digest(x_clinic_key.encode(), settings.api_key.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong X-Clinic-Key header")

    def local_now() -> datetime:
        return now().astimezone(tz).replace(tzinfo=None)

    def free_slots(day: date) -> list[time]:
        current = local_now()
        with db.connect() as conn:
            taken = {
                r["starts_at"]
                for r in conn.execute(
                    "SELECT starts_at FROM appointments WHERE status = 'booked' AND starts_at LIKE ?",
                    (f"{day.isoformat()}T%",),
                )
            }
        return [
            t
            for t in schedule.all_slots(day)
            if datetime.combine(day, t) > current and _key(day, t) not in taken
        ]

    def next_openings(after: date, count: int = 3) -> list[dict]:
        found = []
        day = after
        for _ in range(14):
            slots = free_slots(day)
            if slots:
                found.append({"date": day.isoformat(), "times": [schedule.spoken_time(t) for t in slots[:4]]})
                if len(found) == count:
                    break
            day += timedelta(days=1)
        return found

    def check_slot(day: date | None, t: time | None) -> str | None:
        """Returns a sentence explaining why the slot can't be booked, or None if it is bookable."""
        if day is None:
            return "I couldn't read that date. Please send it as YYYY-MM-DD."
        if t is None:
            return "I couldn't read that time. Please send it like 14:30 or 2:30 PM."
        if t not in schedule.all_slots(day):
            return f"The clinic has no appointment starting at {schedule.spoken_time(t)} on {schedule.spoken_date(day)}."
        if datetime.combine(day, t) <= local_now():
            return "That time has already passed."
        if t not in free_slots(day):
            return f"{schedule.spoken_time(t)} on {schedule.spoken_date(day)} is already booked."
        return None

    def refusal(message: str, around: date | None) -> dict:
        start = max(around or local_now().date(), local_now().date())
        return {"booked": False, "message": message, "alternatives": next_openings(start)}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/today", dependencies=[Depends(require_key)])
    def today():
        current = local_now()
        return {
            "date": current.date().isoformat(),
            "weekday": current.strftime("%A"),
            "time": schedule.spoken_time(current.time()),
            "timezone": settings.timezone,
        }

    @app.get("/slots", dependencies=[Depends(require_key)])
    def slots(date: str | None = None):
        if not date:
            openings = next_openings(local_now().date())
            return {"openings": openings, "message": _describe_openings(openings)}
        day = schedule.parse_date(date)
        if day is None:
            return {"date": date, "times": [], "message": "I couldn't read that date. Please send it as YYYY-MM-DD."}
        times = [schedule.spoken_time(t) for t in free_slots(day)]
        if times:
            message = f"Open times on {schedule.spoken_date(day)}: {', '.join(times)}."
        else:
            openings = next_openings(day + timedelta(days=1))
            message = f"Nothing is open on {schedule.spoken_date(day)}. {_describe_openings(openings)}"
        return {"date": day.isoformat(), "times": times, "message": message}

    @app.post("/appointments", dependencies=[Depends(require_key)])
    def book(body: BookRequest):
        day, t = schedule.parse_date(body.date), schedule.parse_time(body.time)
        problem = check_slot(day, t)
        if problem:
            return refusal(problem, day)
        try:
            with db.connect() as conn:
                cur = conn.execute(
                    "INSERT INTO appointments (patient_name, phone, reason, starts_at, created_at) VALUES (?, ?, ?, ?, ?)",
                    (body.patient_name.strip(), schedule.normalise_phone(body.phone), body.reason.strip(),
                     _key(day, t), local_now().isoformat(timespec="seconds")),
                )
                appointment_id = cur.lastrowid
        except sqlite3.IntegrityError:
            return refusal(f"{schedule.spoken_time(t)} on {schedule.spoken_date(day)} was just taken.", day)
        return {
            "booked": True,
            "appointment_id": appointment_id,
            "message": f"Booked {body.patient_name.strip()} for {schedule.spoken_date(day)} at {schedule.spoken_time(t)}.",
        }

    @app.get("/appointments", dependencies=[Depends(require_key)])
    def lookup(phone: str):
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, patient_name, reason, starts_at FROM appointments "
                "WHERE phone = ? AND status = 'booked' AND starts_at > ? ORDER BY starts_at",
                (schedule.normalise_phone(phone), local_now().isoformat(timespec="minutes")),
            ).fetchall()
        appointments = [_describe(r) for r in rows]
        if not appointments:
            message = "No upcoming appointments for that phone number."
        else:
            message = "Upcoming: " + "; ".join(
                f"#{a['appointment_id']} {a['when']} for {a['reason']}" for a in appointments
            ) + "."
        return {"appointments": appointments, "message": message}

    def owned_booking(appointment_id: int, phone: str) -> sqlite3.Row | None:
        with db.connect() as conn:
            return conn.execute(
                "SELECT * FROM appointments WHERE id = ? AND phone = ? AND status = 'booked'",
                (appointment_id, schedule.normalise_phone(phone)),
            ).fetchone()

    @app.post("/appointments/{appointment_id}/reschedule", dependencies=[Depends(require_key)])
    def reschedule(appointment_id: int, body: RescheduleRequest):
        row = owned_booking(appointment_id, body.phone)
        if row is None:
            return {"booked": False, "message": "I couldn't find that appointment for this phone number."}
        day, t = schedule.parse_date(body.date), schedule.parse_time(body.time)
        problem = check_slot(day, t)
        if problem:
            return refusal(problem, day)
        try:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE appointments SET starts_at = ? WHERE id = ?", (_key(day, t), appointment_id)
                )
        except sqlite3.IntegrityError:
            return refusal(f"{schedule.spoken_time(t)} on {schedule.spoken_date(day)} was just taken.", day)
        return {
            "booked": True,
            "appointment_id": appointment_id,
            "message": f"Moved to {schedule.spoken_date(day)} at {schedule.spoken_time(t)}.",
        }

    @app.post("/appointments/{appointment_id}/cancel", dependencies=[Depends(require_key)])
    def cancel(appointment_id: int, body: CancelRequest):
        row = owned_booking(appointment_id, body.phone)
        if row is None:
            return {"cancelled": False, "message": "I couldn't find that appointment for this phone number."}
        with db.connect() as conn:
            conn.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appointment_id,))
        return {"cancelled": True, "message": f"Cancelled the appointment on {_describe(row)['when']}."}

    @app.post("/webhooks/bland")
    async def bland_webhook(request: Request):
        if not settings.webhook_secret:
            raise HTTPException(status_code=503, detail="BLAND_WEBHOOK_SECRET is not set")
        raw = await request.body()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="body is not JSON")
        signature = request.headers.get("x-webhook-signature", "")
        if not _signature_ok(settings.webhook_secret, raw, payload, signature):
            raise HTTPException(status_code=401, detail="bad X-Webhook-Signature")

        call_id = payload.get("call_id")
        if not call_id:
            raise HTTPException(status_code=400, detail="no call_id")
        received_at = local_now().isoformat(timespec="seconds")
        if payload.get("event_type") == "dispositions":
            db.add_dispositions(call_id, received_at, payload.get("agent_dispositions") or [])
            return {"ok": True}
        # Bland does not retry the call webhook, but the same call can arrive twice if
        # resent by hand, so this is an upsert keyed on call_id.
        db.upsert_call({
            "call_id": call_id,
            "received_at": received_at,
            "status": payload.get("status"),
            "call_length": payload.get("call_length"),
            "summary": payload.get("summary"),
            "transcript": payload.get("concatenated_transcript"),
            "payload": json.dumps(payload),
        })
        if payload.get("agent_dispositions"):
            db.add_dispositions(call_id, received_at, payload["agent_dispositions"])
        return {"ok": True}

    @app.get("/calls", dependencies=[Depends(require_key)])
    def calls(limit: int = 20):
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT call_id, received_at, status, call_length, summary, outcome, dispositions "
                "FROM calls ORDER BY received_at DESC LIMIT ?",
                (min(limit, 200),),
            ).fetchall()
        return {"calls": [dict(r) for r in rows]}

    return app


def _key(day: date, t: time) -> str:
    return f"{day.isoformat()}T{t.strftime('%H:%M')}"


def _describe(row: sqlite3.Row) -> dict:
    starts = datetime.fromisoformat(row["starts_at"])
    return {
        "appointment_id": row["id"],
        "patient_name": row["patient_name"],
        "reason": row["reason"],
        "starts_at": row["starts_at"],
        "when": f"{schedule.spoken_date(starts.date())} at {schedule.spoken_time(starts.time())}",
    }


def _describe_openings(openings: list[dict]) -> str:
    if not openings:
        return "There are no openings in the next two weeks."
    parts = []
    for o in openings:
        day = date.fromisoformat(o["date"])
        parts.append(f"{schedule.spoken_date(day)}: {', '.join(o['times'])}")
    return "Next openings. " + ". ".join(parts) + "."


def _signature_ok(secret: str, raw: bytes, payload: object, signature: str) -> bool:
    """Bland signs with HMAC-SHA256, hex encoded. Its docs sign JSON.stringify(body), which
    should equal the raw bytes; the compact re-serialisation is checked too in case it doesn't."""
    if not signature:
        return False
    candidates = [raw, json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()]
    for body in candidates:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature.strip().lower()):
            return True
    return False
