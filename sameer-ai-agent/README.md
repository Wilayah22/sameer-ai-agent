# sameer-ai-agent

Python Flask REST API for generating Arabic family-discussion questions and
saving session ratings.

## Run

```bash
python sameer-ai-agent/app.py
```

The app listens on `0.0.0.0:8080`.

Set `GEMINI_API_KEY` in the environment before calling `GET /get_question`.

## Endpoints

- `GET /get_question` — generates a new Arabic question using the last 10 sessions.
- `POST /save_rating` — accepts `{ "topic": "...", "category": "...", "rating": ... }`.