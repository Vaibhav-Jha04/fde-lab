# fde-lab

A phone booking agent for a made-up dental clinic, built on [Bland](https://www.bland.ai),
plus open models on [Nebius Token Factory](https://tokenfactory.nebius.com).
The clinic, patients and data are all made up.

| Folder | What it is | Status |
|---|---|---|
| `clinic-api/` | The clinic's booking server. The Bland agent calls it to check slots, book, reschedule and cancel, and Bland sends finished calls to it | built, 29 offline tests |
| `bland/` | The agent's prompt, tools, knowledge base and evaluation cases, kept as files | next |
| `token-factory/` | Porting an app to Token Factory, a model benchmark, a call summariser | later |

The full plan is in [PLAN.md](PLAN.md).

## clinic-api

FastAPI + SQLite. Clinic hours are Monday to Friday 9:00 to 17:00 (closed 12:00 to 13:00)
and Saturday 9:00 to 13:00, in 30-minute slots, New York time.

| Route | Does |
|---|---|
| `GET /health` | liveness check, no key needed |
| `GET /today` | today's date and weekday in clinic time, so the agent can turn "next Tuesday" into a date |
| `GET /slots?date=YYYY-MM-DD` | free times that day; without a date, the next three days with openings |
| `POST /appointments` | book: `patient_name`, `phone`, `reason`, `date`, `time` |
| `GET /appointments?phone=` | a caller's upcoming appointments |
| `POST /appointments/{id}/reschedule` | move it: `phone`, `date`, `time` |
| `POST /appointments/{id}/cancel` | cancel it: `phone` |
| `POST /webhooks/bland` | Bland's post-call webhook; stores each call once |
| `GET /calls` | stored calls |

Design choices:

- **Every response has a `message` the agent can read out**, for example
  "9:30 AM on Wednesday 18 November is already booked", plus alternatives.
- **A refused booking is HTTP 200 with `booked: false`, not a 4xx.** When a request fails,
  Bland only passes `ok` and `message` to the agent, so a 409 would hide the reason and the
  alternative times. 4xx is kept for a missing key or a malformed request.
- **Times are read leniently:** `14:30`, `2:30 PM`, `2:30pm` and `2:30 p.m.` all work. Phone
  numbers are compared by their last 10 digits, so `(555) 010-9999` matches `+1 555 010 9999`.
- **Rescheduling and cancelling need the phone number on the booking**, so one caller can't
  change another caller's appointment by guessing an id.
- **Double booking is blocked by the database itself** (a unique index on booked slots), so
  two calls racing for the same slot can't both win.
- **Tool routes need an `X-Clinic-Key` header.** The webhook is checked against Bland's
  HMAC-SHA256 signature in `X-Webhook-Signature` instead, and stays off until
  `BLAND_WEBHOOK_SECRET` is set.

### Run it

```bash
cp .env.example .env          # set CLINIC_API_KEY to any long random string
cd clinic-api
uv sync
uv run pytest                 # offline, no keys needed
set -a; . ../.env; set +a
uv run python -m clinic_api.seed                       # a few made-up bookings
uv run uvicorn clinic_api.main:app --port 8000
```

Bland calls the server from the internet, so it needs a public URL. A free Cloudflare
quick tunnel works with no account: `cloudflared tunnel --url http://localhost:8000`.
