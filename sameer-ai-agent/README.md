# sameer-ai-agent

خادم Flask لروبوت "سمير" للحوار العائلي. الجهاز (ESP32-S3 / M5Stack) يرسل طلبات HTTP،
والخادم يختار فئة الجلسة، ويولّد السؤال بـ Google Gemini، ويقرر متى يتدخل سمير،
ويحفظ ملخصًا نصيًا لكل جلسة تقرؤه لوحة "مؤشر الرابطة الأسرية".

## Run

```bash
pip install -r sameer-ai-agent/requirements.txt
export GEMINI_API_KEY=...
python sameer-ai-agent/app.py
```

The app listens on `$PORT` (default `8080`).

| Env var | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Required. Without it Sameer uses built-in fallback questions. |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Gemini model id. |
| `GEMINI_FALLBACK_MODELS` | `gemini-flash-lite-latest,gemini-flash-latest` | Tried in order when `GEMINI_MODEL` is busy (503/429) after one retry. |
| `GEMINI_TTS_MODEL` | `gemini-3.8-flash-tts` | Gemini text-to-speech model for `/tts`. |
| `SAMEER_DEVICE_TOKEN` | — | Optional. When set, `/tts` and `/converse` require it in `X-Device-Token`. |
| `ROBOT_NAME` | `سمير` | The name the robot uses in every prompt, e.g. `حوار`. |
| `SAMEER_TZ` | `Asia/Riyadh` | Time zone for time-of-day category choice. |

## Session flow (device)

```
tap → POST /session_intro          → speak "question"
  during session, on silence or every check_again_seconds:
      POST /session_followup       → "wait" | "follow_up" (speak text) | "wrap_up" (speak text)
end → POST /evaluate_session       (optional, participation numbers)
    → POST /save_rating            (family presses 1–3)
```

### `POST /session_intro` (also `GET`, and `GET /get_question` for older firmware)

If the family prepared a question from the dashboard ("ابدأ جلسة"), that question is used
and `source` is `"dashboard"`.

Body (all optional): `{ "ages": [9, 12], "occasion": "رمضان" }`

```json
{ "session_id": "6b6414aed2b0", "category": "الذكريات", "question": "…", "source": "gemini" }
```

Category is one of الذكريات، الامتنان، الأحلام، الحكايات، القيم, chosen by:
the family's past ratings (higher-rated categories are picked more often), a penalty on the
last three categories used (variety), a bonus for untried categories, and time/occasion
(evenings favour الحكايات, weekends الذكريات; occasions such as رمضان or العيد boost
related categories). `source` is `"fallback"` when Gemini was unavailable.

### `POST /session_followup`

This endpoint takes numbers only (silence and talking); spoken turns go to `/converse`.

```json
{ "session_id": "…", "elapsed_seconds": 90, "silence_seconds": 25, "talking": false }
```

Returns `{"action": "wait", "check_again_seconds": 10}`, `{"action": "follow_up", "text": "…"}`
or `{"action": "wrap_up", "text": "…"}`. Sameer waits while the family talks, steps in only
after 20 s of silence, at most 3 times, at least 60 s apart, and wraps up after 15 minutes.

### `POST /evaluate_session`

```json
{
  "session_id": "…",
  "duration_seconds": 480,
  "silence_seconds": 120,
  "speakers": [{ "talk_seconds": 250, "turns": 12 }, { "talk_seconds": 60, "turns": 5 }]
}
```

`speakers` are talk-time totals; send `[]` if the device can't tell voices apart. Each may carry
an optional `"member": "الأب"` (a role from the family settings) for the dashboard's
participation card.
Returns a 1–3 `score`, `talk_ratio`, `balance` (0–1, how evenly people spoke), and Arabic
`suggestions`. The first suggestion is passed to Gemini for the next question. When a session
has no manual rating, this score counts in its place.

### `POST /save_rating`

`{ "session_id": "…", "rating": 3 }`. Rating must be 1, 2 or 3.
Older form still works: `{ "topic": "…", "rating": 2, "category": "…" }`.

### `GET|POST /family`

`{ "name": "عائلة السالم", "members": [{ "role": "الأب", "age": 44 }, { "role": "الابنة", "age": 9 }] }`.
Members under 18 give Gemini the children's ages. Editable from the dashboard settings.

## Dashboard (`/dashboard`)

The dashboard reads `GET /api/dashboard`: the family bond index (same formula as before:
sessions up to 20 → 40 pts, average rating of 3 → 45, categories of 5 → 15, over the last
30 days) with its 30-day trend, KPIs compared with the previous 30 days, engagement per
category, participation per family member, recent sessions, and "ملاحظة من سمير", which
Gemini writes from these numbers (cached until a new session completes; rule-based fallback).

Until the family completes its first session, it shows a clearly-labelled demo family.

- `POST /api/suggestion` — a new suggested question ("استكشف سؤالًا آخر").
- `POST /api/suggestion/queue` — "ابدأ جلسة": the next tap on the device asks this question.

### `POST /tts`

`{ "text": "…" }` → Arabic speech from Gemini (`GEMINI_TTS_MODEL`, default `gemini-3.8-flash-tts`;
voice `GEMINI_TTS_VOICE`, default `Puck`) as raw 16-bit mono PCM, sample rate in the
`X-Sample-Rate` header (24000, the StackChan speaker's rate). When `SAMEER_DEVICE_TOKEN` is set on the
server, requests need the same value in `X-Device-Token`. The robot app lives in `firmware/`.

### `POST /converse?session_id=…`

Body: one spoken turn as WAV (the robot sends 12 kHz mono, at most 12 s). Gemini listens to it with
the session's question and the earlier turns and decides:

- `X-Action: reply`: the body is the robot's spoken answer (PCM, rate in `X-Sample-Rate`).
- `X-Action: listen`: empty body; the family is talking among themselves, so the robot stays quiet.
- `X-Action: wrap_up`: the body says goodbye and asks for the rating; the robot then shows 1–2–3.

`X-Reply` carries the reply text, URL-encoded. If Gemini is unavailable the answer is `listen`.

### `GET /stats`

`total_sessions`, `average_rating_overall`, `average_rating_per_category`, `recent_topics`,
`categories`. Counts only completed sessions (rated or evaluated).

## Privacy

Spoken turns sent to `/converse` are understood in memory and never written to disk. A one-line
summary of each turn is kept in server memory so the robot can follow the conversation, and is
deleted when the session is evaluated or rated (or after an hour).

`memory.json` stores, per session: category, Sameer's own question and follow-ups, rating,
and evaluation numbers. It never stores audio or what the family said.
