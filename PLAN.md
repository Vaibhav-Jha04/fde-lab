# FDE lab: Bland clinic agent + Nebius Token Factory

Practice build: a voice booking agent on Bland and open models on Nebius Token Factory.
Rule: no paid spend. No cards, no phone numbers, no GPUs. Made-up clinic, made-up data.

## Repo layout

```
fde-lab/
  clinic-api/        FastAPI server the Bland agent calls (slots, booking, call records), SQLite
  bland/             agent prompt, tool definitions, knowledge base, test cases, kept as files
  token-factory/     the port, the model benchmark, the call summariser
  NOTES.md           what broke, what I'd ask and how I'd scope a real customer
  README.md          how to run it, the Loom link
```

## Phase 0: accounts and keys (Vaibhav, about 15 min)

- Sign up at app.bland.ai and tokenfactory.nebius.com. No card on either.
- Create a Bland API key and a Token Factory API key; put both in `fde-lab/.env`
  (never committed).
- Decide repo visibility, then the repo gets created with `gh`.

## Phase 1: clinic booking server (`clinic-api/`)

FastAPI + SQLite, made-up clinic "Maple Street Dental".
- `GET /slots?date=YYYY-MM-DD` free appointment times
- `POST /appointments` book (name, phone, reason, time); refuses taken or past slots
- `POST /appointments/{id}/reschedule` and `/cancel`
- `POST /webhooks/bland` stores each finished call as a row (call id, transcript,
  summary, outcome)
- A shared secret header on every route so random internet traffic is refused
- Offline tests with pytest
- Public URL through a free Cloudflare quick tunnel (`cloudflared tunnel --url`),
  no account needed

## Phase 2: the Bland agent (`bland/`)

Built in the dashboard (or through Bland's MCP server from Claude Code), with every
piece also saved as a file in the repo so it is reviewable.
- Prompt: greet, get name, reason, preferred time, check slots, confirm, book
- Two webhook tools: check slots, book appointment (plus reschedule)
- Knowledge base: clinic FAQ (hours, address, insurance, parking, prep for cleanings)
- Guard rail: never gives medical advice; offers a human for anything clinical
- Tune interruptions and response speed
- Test with "Talk in browser" only

## Phase 3: call records into the database

Bland's docs set the post-call webhook either on a phone number (inbound) or on an
outbound API call to a phone number. Browser test calls have neither, so the post-call
webhook will not fire. Two ways to get every call into the database without a number:
- An end-of-call webhook tool ("Save call record") the agent calls before it hangs
  up, posting the caller, reason and result to `POST /calls/record`. This is the
  pattern in Bland's own custom API tutorial.
- A script that reads finished calls from Bland's List Calls API and fills in the
  transcript for each row.
`/webhooks/bland` stays built and tested for a real deployment with a number.

Dispositions (automatic outcome labels) are greyed out on the free account: Bland's
docs say they are enterprise only. Phase 5 labels the outcome with a Token Factory
model instead.

## Credit budget

The free Bland account has 1.94 credits. Bland's docs list $0.14 per connected minute
on the free plan and do not say whether browser calls, chat tests, eval runs or Norm
draw from credits. If one credit is one dollar, that is about 13 minutes of voice.
- Iterate with the text Chat button, keep voice calls for checks and the Loom.
- Build the second agent through the MCP server from Claude Code, not Norm (Norm is
  billed per token).
- Run evals with 1 conversation per test case.
- Check the Billing page after each session; stop and ask before buying anything.

## Phase 4: evaluations

Five simulated callers in Bland Evaluations, each with a judge:
1. Reschedule an existing appointment
2. Angry caller who was double-booked
3. Off-topic caller (asks about the weather, tries to chat)
4. Gives wrong or changing info (wrong date, corrects name)
5. Asks for a human
Run, fix the prompt or tools, rerun until passing. Bland keeps manual eval runs only
in the browser that started them, so export each run's results (and a screenshot)
into `bland/evals/`. Log what each fix was.

## Phase 5: Token Factory (`token-factory/`)

1. Port: move eval-lab's support assistant (48 graded test cases, currently on the
   Anthropic SDK with no key) to Token Factory through the OpenAI SDK. Write down
   every change: client, message format, model names, parameters, tool calling,
   limits hit.
2. Benchmark: run eval-lab's 48 cases on 2 to 3 open models. Record pass rate,
   time to first token, tokens per second, tokens used.
3. Stretch, joins both halves: a call summariser. After each Bland call, a Token
   Factory model writes a short summary for the clinic and labels the outcome
   (booked, rescheduled, needs human, no booking). Stored with the call row.

## Phase 6: hand-in

- `NOTES.md`: what broke, how I'd scope this for a real clinic (with the questions I'd ask
  the clinic first), what I'd do differently with a budget.
- 2-minute Loom (Vaibhav records): talk to the agent, show the booking appear in the
  database, show the eval run, show the benchmark table.

## Read-only items (no build)

SIP, Twilio, built-in integrations, environments, enterprise releases on the Bland
side; GPU VM model hosting, Managed Kubernetes, fine-tuning, the solutions library,
the Token Factory cookbook and Soperator on the Nebius side. One short paragraph each
in NOTES.md.

## Order and rough time

| Phase | Time | Needs |
|---|---|---|
| 0 accounts | 15 min | Vaibhav |
| 1 server | 2 h | nothing |
| 2 agent | 2 to 3 h | Bland account, tunnel |
| 3 call records | 1 h | Phase 2 |
| 4 evals | 2 h | Phase 2 |
| 5 Token Factory | 3 h | Token Factory key |
| 6 hand-in | 1 to 2 h | all |

Phase 1 can start before any account exists. Phase 5 can run in parallel with 2 to 4.
