# sameer-ai-agent

سمير: وكيل ذكاء اصطناعي (Claude) للحوار العائلي. يقترح أسئلة نقاش لأطفال
من 8 إلى 14 سنة، ويتذكر أفراد العائلة، ويحفظ تقييم كل جلسة.

## Run

```bash
pip install -r sameer-ai-agent/requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python sameer-ai-agent/app.py
```

The app listens on `0.0.0.0:8080`. Open `/chat` to talk to Sameer.

## How the agent works

- `agent.py` runs Claude (`claude-opus-5-5`) through the Anthropic SDK tool runner.
  Sameer decides on its own when to call these tools:
  - `get_family_profile` — family members, per-category ratings, last 5 topics
  - `get_recent_sessions` — the latest saved sessions
  - `remember_family_member` — save a member's name, age and interests
  - `save_session` — save a finished discussion with the family's 1–3 rating
- `memory_store.py` reads and writes `memory.json` (`sessions` and `family`).
- Server-side refusal fallback is enabled (`fallbacks: "default"`), so a declined
  request is retried on another model inside the same API call.
- Conversations are kept in the server's memory (up to 200 conversations, 40 turns each)
  and are lost on restart. The family profile and sessions persist in `memory.json`.

## Endpoints

- `GET /chat` — chat page.
- `POST /api/chat` — `{ "message": "...", "conversation_id": "..." }` →
  `{ "conversation_id": "...", "reply": "..." }`. Omit `conversation_id` to start a new conversation.
- `GET /get_question` — one new Arabic question based on the family's memory.
- `POST /save_rating` — accepts `{ "topic": "...", "rating": ..., "category": "..." }`.
  The category is optional and is inferred from the topic when omitted.
- `GET /stats` — total sessions and average ratings overall and per category.
