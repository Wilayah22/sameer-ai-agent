import json
import os
from collections import defaultdict
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, request, render_template
import google.generativeai as genai


app = Flask(__name__)
MEMORY_PATH = Path(__file__).with_name("memory.json")
MEMORY_LOCK = Lock()


def load_memory():
    if not MEMORY_PATH.exists():
        save_memory({"sessions": []})

    with MEMORY_PATH.open("r", encoding="utf-8") as memory_file:
        memory = json.load(memory_file)

    if not isinstance(memory, dict) or not isinstance(memory.get("sessions"), list):
        raise ValueError('memory.json must contain a "sessions" list.')

    return memory


def save_memory(memory):
    with MEMORY_PATH.open("w", encoding="utf-8") as memory_file:
        json.dump(memory, memory_file, ensure_ascii=False, indent=2)
        memory_file.write("\n")


def infer_category(topic):
    text = str(topic or "").strip().lower()
    category_keywords = {
        "دينية": (
            "دين",
            "ديني",
            "قرآن",
            "صلاة",
            "صيام",
            "رمضان",
            "دعاء",
            "مسجد",
            "الله",
            "نبي",
            "religion",
            "quran",
            "prayer",
        ),
        "مهارات تواصل": (
            "تواصل",
            "حوار",
            "نقاش",
            "استماع",
            "تحدث",
            "تعبير",
            "مشاعر",
            "رأي",
            "اعتذار",
            "اعتذر",
            "نعتذر",
            "communication",
            "conversation",
            "listening",
            "feelings",
        ),
        "اجتماعية": (
            "صديق",
            "أصدقاء",
            "عائلة",
            "تعاون",
            "تعاو",
            "مساعدة",
            "مجتمع",
            "جار",
            "زميل",
            "علاقة",
            "خلاف",
            "عائل",
            "friend",
            "family",
            "community",
            "teamwork",
        ),
        "حياتية": (
            "يوم",
            "وقت",
            "نوم",
            "صحة",
            "غذاء",
            "عادة",
            "مسؤولية",
            "مدرسة",
            "منزل",
            "قرار",
            "daily",
            "health",
            "habit",
            "school",
            "responsibility",
        ),
    }

    for category, keywords in category_keywords.items():
        if any(keyword in text for keyword in keywords):
            return category
    return "عام"


def session_category(session):
    category = session.get("category")
    if isinstance(category, str) and category.strip():
        return category.strip()
    return infer_category(session.get("topic"))


def numeric_rating(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_personalization_stats(memory):
    sessions = memory["sessions"]
    category_ratings = defaultdict(list)
    all_ratings = []
    recent_topics = []

    for session in sessions:
        rating = numeric_rating(session.get("rating"))
        if rating is not None:
            category_ratings[session_category(session)].append(rating)
            all_ratings.append(rating)

    for session in reversed(sessions):
        topic = session.get("topic")
        if isinstance(topic, str) and topic.strip() and topic not in recent_topics:
            recent_topics.append(topic.strip())
            if len(recent_topics) == 5:
                break

    average_rating_per_category = {
        category: round(sum(ratings) / len(ratings), 2)
        for category, ratings in category_ratings.items()
    }
    highest_average = (
        max(average_rating_per_category.values())
        if average_rating_per_category
        else None
    )
    highest_rated_categories = [
        category
        for category, average in average_rating_per_category.items()
        if average == highest_average
    ]

    return {
        "total_sessions": len(sessions),
        "average_rating_overall": (
            round(sum(all_ratings) / len(all_ratings), 2) if all_ratings else 0.0
        ),
        "average_rating_per_category": average_rating_per_category,
        "highest_rated_categories": highest_rated_categories,
        "recent_topics": recent_topics,
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.get("/get_question")
def get_question():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"error": "GEMINI_API_KEY is not configured."}), 500

    try:
        with MEMORY_LOCK:
            memory = load_memory()
            recent_sessions = memory["sessions"][-10:]
            personalization = compute_personalization_stats(memory)

        category_averages = personalization["average_rating_per_category"]
        highest_categories = personalization["highest_rated_categories"] or ["عام"]
        recent_topics = personalization["recent_topics"] or ["لا توجد موضوعات سابقة"]

        prompt = f"""
Suggest one new Arabic family-discussion question for children aged 8-14.
The family has historically rated these categories highest:
{json.dumps(highest_categories, ensure_ascii=False)}

Category average ratings:
{json.dumps(category_averages, ensure_ascii=False, indent=2)}

Lean toward the highest-rated categories while still suggesting a fresh,
thoughtful question. Do not repeat any of the family's last 5 topics:
{json.dumps(recent_topics, ensure_ascii=False, indent=2)}

Use the recent sessions below for additional context. The question should be
clear, warm, and suitable for a family conversation.

Recent sessions:
{json.dumps(recent_sessions, ensure_ascii=False, indent=2)}

Return only the Arabic question, with no explanation and no quotation marks.
""".strip()

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-3.8-flash")
        response = model.generate_content(prompt)
        question = response.text.strip()

        if not question:
            return jsonify({"error": "Gemini returned an empty question."}), 502

        return jsonify({"question": question})
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return jsonify({"error": str(error)}), 500
    except Exception as error:
        app.logger.exception("Failed to generate a question")
        return jsonify({"error": str(error)}), 502


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

    category = payload.get("category")
    session = {
        "topic": payload["topic"],
        "category": (
            category.strip()
            if isinstance(category, str) and category.strip()
            else infer_category(payload["topic"])
        ),
        "rating": payload["rating"],
    }
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