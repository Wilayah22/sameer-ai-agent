# sameer-ai-agent

خادم Flask لروبوت "حوار" للحوار العائلي. الجهاز (ESP32-S3 / M5Stack) يرسل طلبات HTTP،
والخادم يختار فئة الجلسة، ويولّد السؤال بـ Google Gemini، ويقرر متى يتدخل حوار،
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
| `GEMINI_LIVE_MODEL` | `gemini-3.8-live` | Gemini Live model for the robot's voice-call conversations. |
| `GEMINI_LIVE_VOICE` | `Puck` | Voice for Live conversations. |
| `SAMEER_DEVICE_TOKEN` | — | Optional. When set, `/tts` and `/converse` require it in `X-Device-Token`. |
| `ROBOT_NAME` | `حوار` | The name the robot uses in every prompt. |
| `DATABASE_URL` | — | Optional Postgres URL (e.g. a free Neon database). Without it sessions live in `memory.json`, which Render's free disk wipes on every deploy. |
| `SAMEER_TZ` | `Asia/Riyadh` | Time zone for time-of-day category choice. |

## Live conversation (device)

```
tap → POST /live/start            → { session_id, category, ws_url, token, setup }
      robot ⇄ Gemini Live (WebSocket, audio both ways; this server is not in the audio path)
      every 8 s: POST /live/heartbeat { session_id, turns, replies, mode?, question?, member?, topics?, speakers?, new_members? }
end → POST /evaluate_session
```

`/live/start` picks the category (same rules as below, or the question queued from the dashboard),
writes the instruction (family ages, last session's tip, safety rules), and creates a Gemini
**ephemeral token** locked to that instruction: a few connections, 35 minutes. The API key never
leaves the server. `setup` is the first WebSocket message; the robot adds a `sessionResumption`
handle when it reconnects. Returns 503 when Live is unavailable, and the robot falls back to the
flow below.

The robot sends `{"mode": "auto"}`: Hiwar greets, asks how everyone is, then asks whether this is a
family or a personal session and calls `set_mode`; the robot passes the answer in
`/live/heartbeat` (`"mode": "family" | "personal"`), and a personal answer moves the session to
`personal_sessions` (a question queued from the dashboard is kept for the next family session).
Without `mode` the session is a family one, as with older firmware.

`{"mode": "personal"}` starts a one-to-one conversation directly: Hiwar asks who is there (from the
family members in the settings), then chats, helps with learning, plays story and word games, or
reflects on the day, adapted to that person's age. Gemini reports who it is talking with
(`set_member`) and the general subject (`note_topic`, one of a fixed list such as المدرسة or
القصص والألعاب). These are stored in `personal_sessions`, apart from family sessions, so they don't
change the family bond index; the dashboard's "الحوارات الشخصية" card shows, per member, how many
conversations, how long, and the subjects. Nothing that was said is stored.

In both modes Hiwar knows the family members from the settings (name, role, age) and addresses
them by name. When it recognises who is speaking it calls `identify_member`; in a family session
those names fill the dashboard's participation card. When someone new introduces themselves
("أنا نورة، عمري 9") it calls `introduce_member`, and the server adds them to the family settings
(or names an existing unnamed member when that is unambiguous).

`/live/heartbeat` keeps the dashboard's live card current: turn counts, and once, the robot's own
opening question (from Gemini's transcription of Hiwar's voice). Nothing the family says is sent.

## Session flow (device, turn-by-turn fallback)

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
after 30 s of silence, at most 6 times, at least 60 s apart; after that only 90 s of silence ends
the session, and it wraps up after 45 minutes.

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

`{ "name": "عائلة السالم", "members": [{ "name": "محمد", "role": "الأب", "age": 44 }, { "name": "نورة", "role": "الابنة", "age": 9 }] }`.
Each member needs a name or a role; the name is how Hiwar addresses them and how the dashboard shows them.
Members under 18 give Gemini the children's ages. Editable from the dashboard settings.

## Dashboard (`/dashboard`)

The dashboard reads `GET /api/dashboard`: the family bond index (same formula as before:
sessions up to 20 → 40 pts, average rating of 3 → 45, categories of 5 → 15, over the last
30 days) with its 30-day trend, KPIs compared with the previous 30 days, engagement per
category, participation per family member, recent sessions, and "ملاحظة من حوار", which
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

### `GET /api/live`

The session happening right now (the newest unrated session with activity in the last 3 minutes):
`{"live": true, "session_id", "category", "question", "elapsed_seconds", "turns", "replies"}`,
or `{"live": false}`. The dashboard polls it every 4 s, shows a "جلسة جارية الآن" card, and
refreshes all its numbers as soon as the session is rated or evaluated.

### `GET /ping`

`{"ok": true}`. The robot calls it when its app opens and every 10 minutes, so a sleeping free
Render instance is awake before the family taps.

### `GET /stats`

`total_sessions`, `average_rating_overall`, `average_rating_per_category`, `recent_topics`,
`categories`. Counts only completed sessions (rated or evaluated).

## Privacy

In a live conversation the robot's audio goes straight to Gemini and never reaches this server.
Gemini's transcript of the family is used on the robot only as a sign that someone is talking
(so a soft voice isn't taken for silence); it is never stored or sent anywhere. Sessions use
context compression, so they can run past Gemini's 15-minute audio limit (up to an hour);
only numbers and the robot's own first question are stored. Spoken turns sent to `/converse`
(fallback mode) are understood in memory and never written to disk. A one-line
summary of each turn is kept in server memory so the robot can follow the conversation, and is
deleted when the session is evaluated or rated (or after an hour).

`memory.json` (or the `DATABASE_URL` database) stores, per session: category, Sameer's own question and follow-ups, rating,
and evaluation numbers. It never stores audio or what the family said.
