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

The device sends numbers only. No audio or speech content ever leaves the device.

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

`speakers` are anonymous talk-time totals; send `[]` if the device can't tell voices apart.
Returns a 1–3 `score`, `talk_ratio`, `balance` (0–1, how evenly people spoke), and Arabic
`suggestions`. The first suggestion is passed to Gemini for the next question. When a session
has no manual rating, this score counts in its place.

### `POST /save_rating`

`{ "session_id": "…", "rating": 3 }`. Rating must be 1, 2 or 3.
Older form still works: `{ "topic": "…", "rating": 2, "category": "…" }`.

### `GET|POST /family`

`{ "ages": [9, 12] }`: the children's ages, used when the device doesn't send them.

### `GET /stats`

`total_sessions`, `average_rating_overall`, `average_rating_per_category`, `recent_topics`,
`categories`. Counts only completed sessions (rated or evaluated).

## Privacy

`memory.json` stores, per session: category, Sameer's own question and follow-ups, rating,
and evaluation numbers. It never stores audio or what the family said.
