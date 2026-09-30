import json

import anthropic
from flask import Flask, jsonify, render_template, request

from agent import ConversationFullError, SameerAgent
from memory_store import (
    MEMORY_LOCK,
    build_session,
    compute_personalization_stats,
    load_memory,
    save_memory,
)

MAX_MESSAGE_CHARS = 2000

app = Flask(__name__)
agent = SameerAgent()


def claude_error_response(error):
    if isinstance(error, anthropic.AuthenticationError):
        return jsonify({"error": "ANTHROPIC_API_KEY is missing or invalid."}), 500
    if isinstance(error, anthropic.RateLimitError):
        return jsonify({"error": "Rate limited by the Claude API. Try again shortly."}), 429
    if isinstance(error, anthropic.APIStatusError):
        app.logger.error("Claude API error %s: %s", error.status_code, error.message)
        return jsonify({"error": "The Claude API returned an error."}), 502
    app.logger.error("Could not reach the Claude API: %s", error)
    return jsonify({"error": "Could not reach the Claude API."}), 502


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.get("/chat")
def chat_page():
    return render_template("chat.html")


@app.post("/api/chat")
def chat():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "Field 'message' must be a non-empty string."}), 400
    if len(message) > MAX_MESSAGE_CHARS:
        return jsonify({"error": f"Message is longer than {MAX_MESSAGE_CHARS} characters."}), 400

    conversation_id = payload.get("conversation_id")
    if not isinstance(conversation_id, str):
        conversation_id = None

    try:
        conversation_id, reply = agent.chat(message.strip(), conversation_id)
    except ConversationFullError as error:
        return jsonify(
            {
                "error": "This conversation is full. Start a new one.",
                "conversation_id": error.conversation_id,
            }
        ), 409
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return jsonify({"error": str(error)}), 500
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as error:
        return claude_error_response(error)

    return jsonify({"conversation_id": conversation_id, "reply": reply})


@app.get("/get_question")
def get_question():
    try:
        question = agent.suggest_question()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return jsonify({"error": str(error)}), 500
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as error:
        return claude_error_response(error)

    if not question:
        return jsonify({"error": "Claude returned an empty question."}), 502
    return jsonify({"question": question})


@app.post("/save_rating")
def save_rating():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    required_fields = ("topic", "rating")
    missing_fields = [field for field in required_fields if field not in payload]
    if missing_fields:
        return jsonify(
            {"error": f"Missing required fields: {', '.join(missing_fields)}."}
        ), 400

    session = build_session(payload["topic"], payload["rating"], payload.get("category"))
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            memory["sessions"].append(session)
            save_memory(memory)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return jsonify({"error": str(error)}), 500

    return jsonify({"status": "saved"})


@app.get("/stats")
def stats():
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            personalization = compute_personalization_stats(memory)

        return jsonify(
            {
                "total_sessions": personalization["total_sessions"],
                "average_rating_overall": personalization["average_rating_overall"],
                "average_rating_per_category": personalization[
                    "average_rating_per_category"
                ],
                "recent_topics": personalization["recent_topics"],
            }
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return jsonify({"error": str(error)}), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8080,
        debug=False,
    )
