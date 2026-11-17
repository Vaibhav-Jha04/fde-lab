import hashlib
import hmac
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from clinic_api.app import Settings, create_app
from clinic_api.db import Database

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 17, 10, 15, tzinfo=TZ)  # a Tuesday
KEY = "test-key"
SECRET = "whsec-test"
H = {"X-Clinic-Key": KEY}


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "clinic.db")


@pytest.fixture
def client(db_path):
    settings = Settings(api_key=KEY, webhook_secret=SECRET, db_path=db_path)
    return TestClient(create_app(settings, clock=lambda: NOW))


def book(client, date="2026-11-18", time="09:00", phone="555-010-9999", name="Jo Park"):
    return client.post(
        "/appointments",
        headers=H,
        json={"patient_name": name, "phone": phone, "reason": "cleaning", "date": date, "time": time},
    ).json()


def sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_clock_is_a_tuesday():
    assert NOW.strftime("%A") == "Tuesday"


def test_health_needs_no_key(client):
    assert client.get("/health").json() == {"ok": True}


@pytest.mark.parametrize("headers", [{}, {"X-Clinic-Key": "wrong"}])
def test_tool_routes_need_the_key(client, headers):
    assert client.get("/slots", params={"date": "2026-11-18"}, headers=headers).status_code == 401
    assert client.post("/appointments", headers=headers, json={}).status_code == 401


def test_today(client):
    assert client.get("/today", headers=H).json()["date"] == "2026-11-17"


def test_slots_today_skip_times_already_passed(client):
    times = client.get("/slots", params={"date": "2026-11-17"}, headers=H).json()["times"]
    assert times[0] == "10:30 AM"
    assert "12:00 PM" not in times  # lunch


def test_saturday_is_mornings_only(client):
    times = client.get("/slots", params={"date": "2026-11-21"}, headers=H).json()["times"]
    assert times == ["9:00 AM", "9:30 AM", "10:00 AM", "10:30 AM", "11:00 AM", "11:30 AM", "12:00 PM", "12:30 PM"]


def test_sunday_is_closed_and_points_to_next_openings(client):
    body = client.get("/slots", params={"date": "2026-11-22"}, headers=H).json()
    assert body["times"] == []
    assert "Monday 23 November" in body["message"]


def test_no_date_lists_next_openings(client):
    body = client.get("/slots", headers=H).json()
    assert body["openings"][0]["date"] == "2026-11-17"


def test_book_then_slot_is_gone(client):
    result = book(client)
    assert result["booked"] is True
    assert "Wednesday 18 November at 9:00 AM" in result["message"]
    times = client.get("/slots", params={"date": "2026-11-18"}, headers=H).json()["times"]
    assert "9:00 AM" not in times


def test_double_booking_is_refused_with_alternatives(client):
    book(client)
    second = book(client, phone="555-010-1111", name="Other Person")
    assert second["booked"] is False
    assert "already booked" in second["message"]
    assert second["alternatives"]


@pytest.mark.parametrize(
    "date,time,expected",
    [
        ("2026-11-18", "12:00", "no appointment starting"),  # lunch
        ("2026-11-17", "09:00", "already passed"),
        ("next tuesday", "09:00", "couldn't read that date"),
        ("2026-11-18", "teatime", "couldn't read that time"),
    ],
)
def test_bad_slots_are_refused_in_plain_words(client, date, time, expected):
    result = book(client, date=date, time=time)
    assert result["booked"] is False
    assert expected in result["message"]


@pytest.mark.parametrize("spoken", ["2:30 PM", "2:30pm", "14:30", "2:30 p.m."])
def test_time_formats_a_voice_agent_may_send(client, spoken):
    assert book(client, time=spoken)["booked"] is True


def test_lookup_ignores_phone_formatting(client):
    book(client, phone="(555) 010-9999")
    body = client.get("/appointments", params={"phone": "+1 555 010 9999"}, headers=H).json()
    assert len(body["appointments"]) == 1


def test_reschedule_needs_the_matching_phone(client):
    appt = book(client)["appointment_id"]
    wrong = client.post(
        f"/appointments/{appt}/reschedule", headers=H,
        json={"phone": "555-000-0000", "date": "2026-11-19", "time": "10:00"},
    ).json()
    assert wrong["booked"] is False


def test_reschedule_moves_and_frees_the_old_slot(client):
    appt = book(client)["appointment_id"]
    moved = client.post(
        f"/appointments/{appt}/reschedule", headers=H,
        json={"phone": "555-010-9999", "date": "2026-11-19", "time": "10:00"},
    ).json()
    assert moved["booked"] is True
    assert "9:00 AM" in client.get("/slots", params={"date": "2026-11-18"}, headers=H).json()["times"]
    assert "10:00 AM" not in client.get("/slots", params={"date": "2026-11-19"}, headers=H).json()["times"]


def test_cancel_frees_the_slot(client):
    appt = book(client)["appointment_id"]
    result = client.post(f"/appointments/{appt}/cancel", headers=H, json={"phone": "5550109999"}).json()
    assert result["cancelled"] is True
    assert "9:00 AM" in client.get("/slots", params={"date": "2026-11-18"}, headers=H).json()["times"]
    assert book(client, phone="555-010-1111")["booked"] is True


def test_database_refuses_two_bookings_for_one_slot(db_path):
    db = Database(db_path)
    row = ("A", "1", "x", "2026-11-18T09:00", "now")
    sql = "INSERT INTO appointments (patient_name, phone, reason, starts_at, created_at) VALUES (?, ?, ?, ?, ?)"
    with db.connect() as conn:
        conn.execute(sql, row)
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute(sql, row)


CALL = {
    "call_id": "c-1",
    "status": "completed",
    "call_length": 1.5,
    "summary": "Booked a cleaning.",
    "concatenated_transcript": "user: hi\nassistant: hello",
}


def post_webhook(client, payload, signature=None):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature is not False:
        headers["X-Webhook-Signature"] = signature or sign(raw)
    return client.post("/webhooks/bland", content=raw, headers=headers)


def test_webhook_rejects_missing_or_bad_signature(client):
    assert post_webhook(client, CALL, signature=False).status_code == 401
    assert post_webhook(client, CALL, signature="00" * 32).status_code == 401


def test_webhook_accepts_signature_of_compact_json(client):
    raw = json.dumps(CALL).encode()  # spaced
    compact = json.dumps(CALL, separators=(",", ":")).encode()
    response = client.post(
        "/webhooks/bland", content=raw,
        headers={"Content-Type": "application/json", "X-Webhook-Signature": sign(compact)},
    )
    assert response.status_code == 200


def test_webhook_stores_call_once_even_if_sent_twice(client):
    assert post_webhook(client, CALL).status_code == 200
    assert post_webhook(client, {**CALL, "summary": "Updated."}).status_code == 200
    calls = client.get("/calls", headers=H).json()["calls"]
    assert len(calls) == 1
    assert calls[0]["summary"] == "Updated."


def test_disposition_events_merge_by_key(client):
    post_webhook(client, CALL)
    for key, value in [("outcome", "booked"), ("sentiment", "calm"), ("outcome", "rescheduled")]:
        post_webhook(client, {
            "call_id": "c-1", "event_type": "dispositions",
            "agent_dispositions": [{"disposition_key": key, "status": "completed", "values": [value]}],
        })
    stored = json.loads(client.get("/calls", headers=H).json()["calls"][0]["dispositions"])
    assert {d["disposition_key"]: d["values"][0] for d in stored} == {"outcome": "rescheduled", "sentiment": "calm"}


def test_webhook_off_until_secret_is_set(db_path):
    settings = Settings(api_key=KEY, webhook_secret=None, db_path=db_path)
    client = TestClient(create_app(settings, clock=lambda: NOW))
    assert post_webhook(client, CALL).status_code == 503
