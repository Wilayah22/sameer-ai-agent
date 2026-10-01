import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, Response, jsonify, render_template, request

import gemini_client
from dashboard_data import build_dashboard, demo_memory, rule_based_insight
from memory_store import (
    CATEGORIES,
    MEMORY_LOCK,
    build_session,
    choose_category,
    compute_personalization_stats,
    family_ages,
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
MAX_MEMBERS = 12
MAX_SPEECH_CHARS = 400
# Optional shared secret for the robot. When set, /tts (which spends Gemini credit) requires it.
DEVICE_TOKEN = os.environ.get("SAMEER_DEVICE_TOKEN", "")

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


def parse_age(value):
    try:
        age = int(float(value))
    except (TypeError, ValueError):
        raise BadRequest("Ages must be numbers.") from None
    if not 1 <= age <= 120:
        raise BadRequest("Each age must be between 1 and 120.")
    return age


def parse_ages(value):
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = [part for part in value.replace("،", ",").split(",") if part.strip()]
    if not isinstance(value, list):
        raise BadRequest("Field 'ages' must be a list of numbers.")
    return [parse_age(age) for age in value]


def parse_members(value):
    if not isinstance(value, list) or len(value) > MAX_MEMBERS:
        raise BadRequest(f"Field 'members' must be a list of up to {MAX_MEMBERS} objects.")
    members = []
    for item in value:
        if not isinstance(item, dict):
            raise BadRequest("Each member must be an object.")
        role = str(item.get("role") or "").strip()[:30]
        if not role:
            raise BadRequest("Each member needs a 'role', e.g. الأب or الابنة.")
        member = {"role": role}
        if item.get("age") not in (None, ""):
            member["age"] = parse_age(item["age"])
        members.append(member)
    return members


@app.errorhandler(BadRequest)
def bad_request(error):
    return jsonify({"error": str(error)}), 400


def pick_question(memory, now, occasion=None, ages=None):
    """Choose a category and have Gemini write a question for it. Returns (category, question, source)."""
    category, reasons = choose_category(memory, now, occasion)
    app.logger.info("Category %s (%s)", category, "، ".join(reasons))
    try:
        question = gemini_client.generate_question(
            category,
            ages or family_ages(memory),
            recent_topics(memory["sessions"], limit=10),
            occasion,
            last_evaluation_tip(memory),
        )
        return category, question, "gemini"
    except gemini_client.GeminiUnavailable as error:
        app.logger.error("Falling back to a built-in question: %s", error)
        return category, gemini_client.FALLBACK_QUESTIONS[category], "fallback"


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/session_intro", methods=["GET", "POST"])
@app.get("/get_question")
def session_intro():
    """Start a session on the device. Uses the question the family queued from the dashboard, if any."""
    payload = json_body() if request.method == "POST" else request.args.to_dict()
    ages = parse_ages(payload.get("ages"))
    occasion = str(payload.get("occasion") or "").strip()[:60] or None
    now = datetime.now(TIMEZONE)

    try:
        with MEMORY_LOCK:
            memory = load_memory()
            queued = memory.get("suggestion") if (memory.get("suggestion") or {}).get("queued") else None
            if queued:
                memory.pop("suggestion")
                save_memory(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    if queued:
        category, question, source = queued["category"], queued["question"], "dashboard"
    else:
        category, question, source = pick_question(memory, now, occasion, ages)

    session = new_session(category, question, now)
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            memory["sessions"].append(session)
            save_memory(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    return jsonify(
        {"session_id": session["id"], "category": category, "question": question, "source": source}
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

    now = datetime.now(TIMEZONE)
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            if session_id:
                session = find_session(memory, session_id)
                if session is None:
                    return jsonify({"error": "Unknown session_id."}), 404
                session["rating"] = rating
                session["rated_at"] = now.isoformat(timespec="seconds")
            else:
                memory["sessions"].append(
                    build_session(payload["topic"], rating, payload.get("category"), now)
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
    members_spoke = sorted(
        {
            str(s["member"]).strip()[:30]
            for s in speakers
            if s.get("member") and number(s, "talk_seconds", 0) > 0
        }
    )
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
            if members_spoke:
                result["members_spoke"] = members_spoke
            session["evaluation"] = result
            save_memory(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    return jsonify(result)


@app.route("/family", methods=["GET", "POST"])
def family():
    """Family name, members' roles and ages. Nothing else about the family is stored."""
    payload = json_body() if request.method == "POST" else {}
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            if request.method == "POST":
                if "name" in payload:
                    memory["family"]["name"] = str(payload["name"] or "").strip()[:40]
                if "members" in payload:
                    memory["family"]["members"] = parse_members(payload["members"])
                if "ages" in payload:
                    memory["family"]["ages"] = parse_ages(payload["ages"]) or []
                save_memory(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500
    return jsonify(memory["family"])


def weekly_insight(memory, facts, now):
    """Sameer's note, written by Gemini from the numbers and cached until a new session completes."""
    key = f"{now.date().isoformat()}:{facts['sessions']}"
    cached = memory.get("insight") or {}
    if cached.get("key") == key:
        return cached["text"], cached["source"]

    if facts["sessions"] >= 3:
        try:
            text, source = gemini_client.generate_insight(facts), "gemini"
        except gemini_client.GeminiUnavailable as error:
            app.logger.error("Falling back to a rule-based insight: %s", error)
            text, source = rule_based_insight(facts), "rules"
    else:
        text, source = rule_based_insight(facts), "rules"

    with MEMORY_LOCK:
        latest = load_memory()
        latest["insight"] = {"key": key, "text": text, "source": source}
        save_memory(latest)
    return text, source


@app.get("/api/dashboard")
def dashboard_data():
    now = datetime.now(TIMEZONE)
    try:
        with MEMORY_LOCK:
            memory = load_memory()
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    data = build_dashboard(memory, now, TIMEZONE)
    demo = data["kpis"]["sessions"] == 0 and not any(
        s["status"] == "completed" for s in data["recent_sessions"]
    )
    if demo:
        data = build_dashboard(demo_memory(now), now, TIMEZONE)
        insight, source = rule_based_insight(data["facts"]), "demo"
    else:
        try:
            insight, source = weekly_insight(memory, data["facts"], now)
        except MEMORY_ERRORS as error:
            return jsonify({"error": str(error)}), 500

    suggestion = memory.get("suggestion")
    data.update(
        demo=demo,
        insight={"text": insight, "source": source},
        suggestion=suggestion,
        generated_at=now.isoformat(timespec="seconds"),
    )
    data.pop("facts")
    return jsonify(data)


@app.post("/api/suggestion")
def new_suggestion():
    """Suggest the next session's question ("استكشف سؤالًا آخر")."""
    now = datetime.now(TIMEZONE)
    try:
        with MEMORY_LOCK:
            memory = load_memory()
        previous = memory.get("suggestion")
        if previous:
            # In-memory only: steers the pick away from the question being replaced.
            memory["sessions"].append({"topic": previous["question"], "category": previous["category"]})
        category, question, source = pick_question(memory, now)
        suggestion = {"category": category, "question": question, "source": source, "queued": False}
        with MEMORY_LOCK:
            latest = load_memory()
            latest["suggestion"] = suggestion
            save_memory(latest)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500
    return jsonify(suggestion)


@app.post("/api/suggestion/queue")
def queue_suggestion():
    """"ابدأ جلسة": the next tap on the device asks this question."""
    try:
        with MEMORY_LOCK:
            memory = load_memory()
        suggestion = memory.get("suggestion")
        if not suggestion:
            category, question, source = pick_question(memory, datetime.now(TIMEZONE))
            suggestion = {"category": category, "question": question, "source": source}
        suggestion["queued"] = True
        with MEMORY_LOCK:
            latest = load_memory()
            latest["suggestion"] = suggestion
            save_memory(latest)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500
    return jsonify(suggestion)


@app.post("/tts")
def tts():
    """Arabic speech for the robot: raw 16-bit mono PCM, sample rate in X-Sample-Rate."""
    if DEVICE_TOKEN and request.headers.get("X-Device-Token") != DEVICE_TOKEN:
        return jsonify({"error": "Invalid device token."}), 401

    text = str(json_body().get("text") or "").strip()
    if not text:
        return jsonify({"error": "Field 'text' is required."}), 400
    if len(text) > MAX_SPEECH_CHARS:
        return jsonify({"error": f"Text is longer than {MAX_SPEECH_CHARS} characters."}), 400

    try:
        pcm, sample_rate = gemini_client.synthesize_speech(text)
    except gemini_client.GeminiUnavailable as error:
        app.logger.error("Speech synthesis failed: %s", error)
        return jsonify({"error": "Speech synthesis is unavailable."}), 502

    return Response(
        pcm,
        mimetype="application/octet-stream",
        headers={"X-Sample-Rate": str(sample_rate), "X-Audio-Format": "pcm_s16le_mono"},
    )


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
