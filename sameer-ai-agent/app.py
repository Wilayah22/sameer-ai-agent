import json
import os
import time
from datetime import datetime
from threading import Lock
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import Flask, Response, jsonify, render_template, request

import gemini_client
from dashboard_data import build_dashboard, demo_memory, rule_based_insight
from memory_store import (
    CATEGORIES,
    GUEST,
    MEMORY_LOCK,
    PERSONAL_TOPICS,
    RELATIONS,
    build_session,
    choose_category,
    compute_personalization_stats,
    family_ages,
    find_personal_session,
    find_session,
    last_evaluation_tip,
    load_memory,
    member_label,
    member_labels,
    new_session,
    parse_rating,
    recent_topics,
    save_memory,
)
from session_logic import CHECK_AGAIN_SECONDS, WRAP_UP_TEXT, decide_intervention, evaluate

TIMEZONE = ZoneInfo(os.environ.get("SAMEER_TZ", "Asia/Riyadh"))
MEMORY_ERRORS = (OSError, ValueError, json.JSONDecodeError)
try:
    import psycopg

    MEMORY_ERRORS += (psycopg.Error,)  # Postgres storage (DATABASE_URL) is unreachable or failing
except ImportError:
    pass
MAX_MEMBERS = 12
MAX_SPEECH_CHARS = 400
# Optional shared secret for the robot. When set, /tts (which spends Gemini credit) requires it.
DEVICE_TOKEN = os.environ.get("SAMEER_DEVICE_TOKEN", "")

# Spoken turns: audio is understood in memory and never stored. A short summary of each turn
# is kept here, in process memory only, so the robot can follow the conversation; it is dropped
# when the session is rated or evaluated, or after an hour.
MAX_TURN_AUDIO_BYTES = 1_500_000
CONVERSATION_TTL_SECONDS = 3600
MAX_TURNS_KEPT = 12
_conversations = {}
_conversations_lock = Lock()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024


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
        name = str(item.get("name") or "").strip()[:30]
        if not role and not name:
            raise BadRequest("Each member needs a name or a role, e.g. نورة or الابنة.")
        member = {"role": role or "أخرى"}
        if name:
            member["name"] = name
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


@app.get("/ping")
def ping():
    # The robot calls this when its app opens and every 10 minutes, so a sleeping free
    # instance is already awake when the family taps for a question.
    return jsonify({"ok": True})


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


LIVE_PLACEHOLDER = "محادثة مباشرة"


def take_queued_suggestion():
    """The question the family prepared from the dashboard ("ابدأ جلسة"), removed once used."""
    with MEMORY_LOCK:
        memory = load_memory()
        queued = memory.get("suggestion") if (memory.get("suggestion") or {}).get("queued") else None
        if queued:
            memory.pop("suggestion")
            save_memory(memory)
    return memory, queued


@app.post("/live/start")
def live_start():
    """Start a live voice conversation: the robot then talks to Gemini directly over a WebSocket.

    Returns a short-lived token locked to this session's instruction (never the API key), and the
    setup message to send first. 503 means Live is unavailable; the robot falls back to /session_intro.
    """
    if not device_authorized():
        return jsonify({"error": "Invalid device token."}), 401
    payload = json_body()
    if payload.get("mode") == "personal":
        return start_personal_live()
    ages = parse_ages(payload.get("ages"))
    occasion = str(payload.get("occasion") or "").strip()[:60] or None
    now = datetime.now(TIMEZONE)

    try:
        memory, queued = take_queued_suggestion()
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    if queued:
        category, question = queued["category"], queued["question"]
    else:
        category, reasons = choose_category(memory, now, occasion)
        app.logger.info("Live category %s (%s)", category, "، ".join(reasons))
        question = None

    instruction = gemini_client.live_instruction(
        category,
        ages or family_ages(memory),
        [t for t in recent_topics(memory["sessions"], limit=10) if t != LIVE_PLACEHOLDER],
        last_evaluation_tip(memory),
        opening_question=question,
        family_name=memory["family"].get("name"),
        members=memory["family"]["members"],
    )
    tools = gemini_client.live_tools("family", member_labels(memory), relations=RELATIONS)
    try:
        live = gemini_client.start_live(instruction, tools)
    except gemini_client.GeminiUnavailable as error:
        app.logger.error("Live conversation unavailable: %s", error)
        return jsonify({"error": str(error)}), 503

    session = new_session(category, question or LIVE_PLACEHOLDER, now)
    session["mode"] = "live"
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            memory["sessions"].append(session)
            save_memory(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    return jsonify({"session_id": session["id"], "category": category, "mode": "family", **live})


def start_personal_live():
    """A one-to-one conversation: Hiwar asks who is there, then chats, teaches, plays or reflects.

    Stored apart from family sessions (it doesn't change the family bond index). The dashboard
    shows who talked, for how long, and the general subjects only.
    """
    now = datetime.now(TIMEZONE)
    try:
        with MEMORY_LOCK:
            memory = load_memory()
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    members = memory["family"]["members"]
    tools = gemini_client.live_tools("personal", member_labels(memory) + [GUEST], PERSONAL_TOPICS, RELATIONS)
    instruction = gemini_client.personal_instruction(members, PERSONAL_TOPICS, GUEST)
    try:
        live = gemini_client.start_live(instruction, tools)
    except gemini_client.GeminiUnavailable as error:
        app.logger.error("Personal live conversation unavailable: %s", error)
        return jsonify({"error": str(error)}), 503

    session = {
        "id": new_session("", "", now)["id"],
        "mode": "personal",
        "started_at": now.isoformat(timespec="seconds"),
        "member": None,
        "topics": [],
    }
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            memory["personal_sessions"].append(session)
            save_memory(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500
    return jsonify({"session_id": session["id"], "mode": "personal", **live})


def add_introduced_members(memory, introduced):
    """Family members who introduced themselves to Hiwar by voice join the family settings."""
    if not isinstance(introduced, list):
        return
    members = memory["family"]["members"]
    for item in introduced[:MAX_MEMBERS]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:30]
        if not name or name == GUEST or len(members) >= MAX_MEMBERS:
            continue
        if any(name in (m.get("name"), m.get("role")) for m in members):
            continue
        relation = item.get("relation") if item.get("relation") in RELATIONS else "أخرى"
        member = {"role": relation, "name": name}
        age = item.get("age")
        if isinstance(age, int) and not isinstance(age, bool) and 1 <= age <= 120:
            member["age"] = age
        # A known role without a name ("الابنة", 9) is the same person: give it the name.
        # Only when it is unambiguous: one unnamed member with that role (and age, if both are known).
        same = [
            m for m in members
            if not m.get("name") and m.get("role") == relation and relation != "أخرى"
            and (m.get("age") is None or "age" not in member or m["age"] == member["age"])
        ]
        same = same[0] if len(same) == 1 else None
        if same is not None:
            same["name"] = name
            if "age" in member:
                same["age"] = member["age"]
        else:
            members.append(member)
        app.logger.info("Hiwar met a family member by voice (%s)", relation)


@app.post("/live/heartbeat")
def live_heartbeat():
    """Numbers from a live conversation (turns so far), plus the robot's own opening question.

    Keeps the dashboard's live card current. Nothing the family says is sent here.
    """
    if not device_authorized():
        return jsonify({"error": "Invalid device token."}), 401
    payload = json_body()
    session_id = payload.get("session_id")
    question = str(payload.get("question") or "").strip()[:300]
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            session = find_session(memory, session_id) or find_personal_session(memory, session_id)
            if session is None:
                return jsonify({"error": "Unknown session_id."}), 404
            session["last_activity"] = datetime.now(TIMEZONE).isoformat(timespec="seconds")
            add_introduced_members(memory, payload.get("new_members"))
            known = set(member_labels(memory))
            if session.get("mode") != "personal":
                speakers = payload.get("speakers")
                if isinstance(speakers, list):
                    spoke = session.setdefault("members_spoke", [])
                    spoke.extend(m for m in speakers if m in known and m not in spoke)
            else:
                if payload.get("member") in known | {GUEST}:
                    session["member"] = payload["member"]
                topics = payload.get("topics")
                if isinstance(topics, list):
                    for topic in topics:
                        if topic in PERSONAL_TOPICS and topic not in session["topics"]:
                            session["topics"].append(topic)
            for key in ("turns", "replies"):
                value = payload.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000:
                    session[key] = max(session.get(key, 0), value)
            if question and session.get("topic") == LIVE_PLACEHOLDER:
                session["topic"] = question
            save_memory(memory)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500
    return jsonify({"status": "ok"})


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

    touch_session(session_id)
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


LIVE_WINDOW_SECONDS = 180  # a session with activity this recent shows as "live" on the dashboard


def touch_session(session_id, **increments):
    """Record that a session is active, and bump activity counters (numbers only, never content)."""
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            session = find_session(memory, session_id)
            if session is None:
                return
            session["last_activity"] = datetime.now(TIMEZONE).isoformat(timespec="seconds")
            for key, amount in increments.items():
                session[key] = session.get(key, 0) + amount
            save_memory(memory)
    except MEMORY_ERRORS as error:
        app.logger.error("Could not record session activity: %s", error)


def forget_conversation(session_id):
    with _conversations_lock:
        _conversations.pop(session_id, None)


def device_authorized():
    return not DEVICE_TOKEN or request.headers.get("X-Device-Token") == DEVICE_TOKEN


@app.post("/converse")
def converse():
    """One spoken turn from the robot: WAV in, the robot's spoken reply (PCM) out.

    X-Action is "reply", "listen" (stay quiet; empty body) or "wrap_up" (say goodbye, then rate).
    """
    if not device_authorized():
        return jsonify({"error": "Invalid device token."}), 401

    session_id = request.args.get("session_id", "")
    audio = request.get_data(cache=False)
    if len(audio) < 44 or not audio.startswith(b"RIFF"):
        return jsonify({"error": "Body must be a WAV file."}), 400
    if len(audio) > MAX_TURN_AUDIO_BYTES:
        return jsonify({"error": "Audio is too long."}), 413

    try:
        with MEMORY_LOCK:
            session = find_session(load_memory(), session_id)
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500
    if session is None:
        return jsonify({"error": "Unknown session_id."}), 404
    if "rating" in session:
        return jsonify({"error": "This session has already been rated."}), 409

    now = time.monotonic()
    with _conversations_lock:
        for key in [k for k, v in _conversations.items() if now - v["updated"] > CONVERSATION_TTL_SECONDS]:
            del _conversations[key]
        turns = list(_conversations.get(session_id, {}).get("turns", []))

    try:
        result = gemini_client.converse(audio, session.get("category", ""), session.get("topic", ""), turns)
    except gemini_client.GeminiUnavailable as error:
        app.logger.error("Could not understand the turn, staying quiet: %s", error)
        return Response(b"", mimetype="application/octet-stream", headers={"X-Action": "listen"})

    with _conversations_lock:
        entry = _conversations.setdefault(session_id, {"turns": [], "updated": now})
        entry["turns"] = (entry["turns"] + [{"heard": result["heard"], "reply": result["reply"] if result["action"] != "listen" else ""}])[-MAX_TURNS_KEPT:]
        entry["updated"] = now
    touch_session(session_id, turns=1, replies=0 if result["action"] == "listen" else 1)

    pcm, sample_rate = b"", gemini_client.TTS_SAMPLE_RATE
    if result["action"] != "listen":
        try:
            pcm, sample_rate = gemini_client.synthesize_speech(result["reply"], timeout_ms=14_000)
        except gemini_client.GeminiUnavailable as error:
            app.logger.error("Speech synthesis failed for a reply: %s", error)

    app.logger.info("Turn %s: %s", session_id, result["action"])
    return Response(
        pcm,
        mimetype="application/octet-stream",
        headers={
            "X-Action": result["action"],
            "X-Sample-Rate": str(sample_rate),
            "X-Reply": quote(result["reply"]),
        },
    )


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
                forget_conversation(session_id)
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
            personal = find_personal_session(memory, session_id)
            if personal is not None:
                # Personal conversations aren't scored: only their length is kept.
                personal["duration_seconds"] = round(duration)
                personal["ended"] = True
                save_memory(memory)
                return jsonify({"status": "saved", "duration_seconds": round(duration)})
            session = find_session(memory, session_id)
            if session is None:
                return jsonify({"error": "Unknown session_id."}), 404
            result = evaluate(duration, silence, speakers, len(session.get("follow_ups", [])))
            members_spoke = members_spoke or session.get("members_spoke")
            if members_spoke:
                result["members_spoke"] = members_spoke
            session["evaluation"] = result
            save_memory(memory)
            forget_conversation(session_id)
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
    # Real data as soon as the family has started any session; the demo family only before that.
    demo = not data["recent_sessions"] and not data["personal"]["sessions"]
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


@app.get("/api/live")
def live_session():
    """The session happening right now, if any: polled by the dashboard every few seconds."""
    now = datetime.now(TIMEZONE)
    try:
        with MEMORY_LOCK:
            memory = load_memory()
    except MEMORY_ERRORS as error:
        return jsonify({"error": str(error)}), 500

    candidates = [s for s in memory["sessions"] if "rating" not in s and "evaluation" not in s]
    candidates += [s for s in memory["personal_sessions"] if not s.get("ended")]
    candidates.sort(key=lambda s: s.get("last_activity") or s.get("started_at") or "")
    for session in reversed(candidates):
        stamp = session.get("last_activity") or session.get("started_at")
        if not stamp:
            continue
        try:
            last = datetime.fromisoformat(stamp)
            started = datetime.fromisoformat(session.get("started_at") or stamp)
        except ValueError:
            continue
        last = last if last.tzinfo else last.replace(tzinfo=TIMEZONE)
        started = started if started.tzinfo else started.replace(tzinfo=TIMEZONE)
        if (now - last).total_seconds() > LIVE_WINDOW_SECONDS:
            continue
        if session.get("mode") == "personal":
            return jsonify(
                {
                    "live": True,
                    "mode": "personal",
                    "session_id": session.get("id"),
                    "member": session.get("member"),
                    "topics": session.get("topics", []),
                    "started_at": session.get("started_at"),
                    "elapsed_seconds": max(0, int((now - started).total_seconds())),
                    "turns": session.get("turns", 0),
                    "replies": session.get("replies", 0),
                }
            )
        return jsonify(
            {
                "live": True,
                "mode": "family",
                "session_id": session.get("id"),
                "category": session.get("category"),
                "question": session.get("topic"),
                "started_at": session.get("started_at"),
                "elapsed_seconds": max(0, int((now - started).total_seconds())),
                "turns": session.get("turns", 0),
                "replies": session.get("replies", 0) + len(session.get("follow_ups", [])),
            }
        )
    return jsonify({"live": False})


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
    if not device_authorized():
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
