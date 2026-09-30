import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request

import gemini_client
from memory_store import (
    CATEGORIES,
    MEMORY_LOCK,
    build_session,
    choose_category,
    compute_personalization_stats,
    find_session,
    last_evaluation_tip,
    load_memory,
    new_session,
    parse_rating,
    recent_topics,
    save_memory,
)
from session_logic import CHECK_AGAIN_SECONDS, WRAP_UP_TEXT, decide_intervention, evaluate

TIMEZONE = ZoneInfo(os.environ.get("SAMEER_TZ", "Asia/Riyadh"))
MEMORY_ERRORS = (OSError, ValueError, json.JSONDecodeError)

app = Flask(__name__)


class BadRequest(Exception):
    pass


def json_body():
    payload = request.get_json(silent=True)
    if payload is None and not request.data:
        return {}
    if not isinstance(payload, dict):
        raise BadRequest("Request body must be a JSON object.")
    return payload


def number(payload, key, default=None):
    value = payload.get(key, default)
    if value is None:
        if default is None:
            raise BadRequest(f"Field '{key}' is required.")
        return default
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise BadRequest(f"Field '{key}' must be a number.") from None
    if value < 0:
        raise BadRequest(f"Field '{key}' must not be negative.")
    return value


def parse_ages(value):
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = [part for part in value.replace("،", ",").split(",") if part.strip()]
    if not isinstance(value, list):
        raise BadRequest("Field 'ages' must be a list of numbers.")
    try:
        ages = [int(float(age)) for age in value]
    except (TypeError, ValueError):
        raise BadRequest("Field 'ages' must be a list of numbers.") from None
    if any(not 1 <= age <= 99 for age in ages):
        raise BadRequest("Each age must be between 1 and 99.")
    return ages


@app.errorhandler(BadRequest)
def bad_request(error):
    return jsonify({"error": str(error)}), 400


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/session_intro", methods=["GET", "POST"])
@app.get("/get_question")
def session_intro():
    """Start a session: pick a category, generate a question, and record the session."""
    payload = json_body() if request.method == "POST" else request.args.to_dict()
    ages = parse_ages(payload.get("ages"))
    occasion = str(payload.get("occasion") or "").strip()[:60] or None
    now = datetime.now(TIMEZONE)

    try:
        with MEMORY_LOCK:
            memory = load_memory()
        category, reasons = choose_category(memory, now, occasion)
        ages = ages or memory["family"]["ages"]
        avoid = recent_topics(memory["sessions"], limit=10)
        tip = last_evaluation_tip(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    source = "gemini"
    try:
        question = gemini_client.generate_question(category, ages, avoid, occasion, tip)
    except gemini_client.GeminiUnavailable as error:
        app.logger.error("Falling back to a built-in question: %s", error)
        question = gemini_client.FALLBACK_QUESTIONS[category]
        source = "fallback"

    session = new_session(category, question, now)
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            memory["sessions"].append(session)
            save_memory(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    app.logger.info("Session %s: %s (%s)", session["id"], category, "، ".join(reasons))
    return jsonify(
        {
            "session_id": session["id"],
            "category": category,
            "question": question,
            "source": source,
        }
    )


@app.post("/session_followup")
def session_followup():
    """Called by the device during a session with simple engagement numbers only."""
    payload = json_body()
    session_id = payload.get("session_id")
    elapsed = number(payload, "elapsed_seconds")
    silence = number(payload, "silence_seconds", 0)
    talking = bool(payload.get("talking", False))

    try:
        with MEMORY_LOCK:
            session = find_session(load_memory(), session_id)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500
    if session is None:
        return jsonify({"error": "Unknown session_id."}), 404
    if "rating" in session:
        return jsonify({"error": "This session has already been rated."}), 409

    follow_ups = session.get("follow_ups", [])
    action = decide_intervention(
        elapsed,
        silence,
        talking,
        len(follow_ups),
        follow_ups[-1]["at"] if follow_ups else None,
    )
    if action == "wait":
        return jsonify({"action": "wait", "check_again_seconds": CHECK_AGAIN_SECONDS})
    if action == "wrap_up":
        return jsonify({"action": "wrap_up", "text": WRAP_UP_TEXT})

    try:
        text = gemini_client.generate_follow_up(
            session["category"], session["topic"], [f["text"] for f in follow_ups]
        )
    except gemini_client.GeminiUnavailable as error:
        app.logger.error("Falling back to a built-in follow-up: %s", error)
        text = gemini_client.FALLBACK_FOLLOW_UP

    try:
        with MEMORY_LOCK:
            memory = load_memory()
            stored = find_session(memory, session_id)
            if stored is not None:
                stored.setdefault("follow_ups", []).append({"at": round(elapsed), "text": text})
                save_memory(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    return jsonify({"action": "follow_up", "text": text, "check_again_seconds": CHECK_AGAIN_SECONDS})


@app.post("/save_rating")
def save_rating():
    payload = json_body()
    rating = parse_rating(payload.get("rating"))
    if rating is None:
        return jsonify({"error": "Field 'rating' must be 1, 2, or 3."}), 400

    session_id = payload.get("session_id")
    if not session_id and not payload.get("topic"):
        return jsonify({"error": "Send either 'session_id' or 'topic'."}), 400

    try:
        with MEMORY_LOCK:
            memory = load_memory()
            if session_id:
                session = find_session(memory, session_id)
                if session is None:
                    return jsonify({"error": "Unknown session_id."}), 404
                session["rating"] = rating
                session["rated_at"] = datetime.now(TIMEZONE).isoformat(timespec="seconds")
            else:
                memory["sessions"].append(
                    build_session(payload["topic"], rating, payload.get("category"))
                )
            save_memory(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    return jsonify({"status": "saved"})


@app.post("/evaluate_session")
def evaluate_session():
    """Analyse participation numbers from the device and suggest improvements."""
    payload = json_body()
    session_id = payload.get("session_id")
    duration = number(payload, "duration_seconds")
    silence = payload.get("silence_seconds")
    silence = None if silence is None else number(payload, "silence_seconds")

    speakers = payload.get("speakers", [])
    if not isinstance(speakers, list) or not all(isinstance(s, dict) for s in speakers):
        return jsonify({"error": "Field 'speakers' must be a list of objects."}), 400
    speakers = [
        {"talk_seconds": number(s, "talk_seconds", 0), "turns": number(s, "turns", 0)}
        for s in speakers
    ]

    try:
        with MEMORY_LOCK:
            memory = load_memory()
            session = find_session(memory, session_id)
            if session is None:
                return jsonify({"error": "Unknown session_id."}), 404
            result = evaluate(duration, silence, speakers, len(session.get("follow_ups", [])))
            session["evaluation"] = result
            save_memory(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    return jsonify(result)


@app.route("/family", methods=["GET", "POST"])
def family():
    """The children's ages, used to tailor questions. Nothing else about the family is stored."""
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            if request.method == "POST":
                ages = parse_ages(json_body().get("ages")) or []
                memory["family"]["ages"] = ages
                save_memory(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500
    return jsonify(memory["family"])


@app.get("/stats")
def stats():
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            personalization = compute_personalization_stats(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    return jsonify(
        {
            "total_sessions": personalization["total_sessions"],
            "average_rating_overall": personalization["average_rating_overall"],
            "average_rating_per_category": personalization["average_rating_per_category"],
            "recent_topics": personalization["recent_topics"],
            "categories": list(CATEGORIES),
        }
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        debug=False,
    )
