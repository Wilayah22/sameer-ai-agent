"""Sameer's family memory: sessions, ratings and family members, stored in memory.json."""

import json
from collections import defaultdict
from pathlib import Path
from threading import Lock

MEMORY_PATH = Path(__file__).with_name("memory.json")
MEMORY_LOCK = Lock()

CATEGORY_KEYWORDS = {
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


def load_memory():
    if not MEMORY_PATH.exists():
        save_memory({"sessions": [], "family": []})

    with MEMORY_PATH.open("r", encoding="utf-8") as memory_file:
        memory = json.load(memory_file)

    if not isinstance(memory, dict) or not isinstance(memory.get("sessions"), list):
        raise ValueError('memory.json must contain a "sessions" list.')
    if not isinstance(memory.get("family"), list):
        memory["family"] = []

    return memory


def save_memory(memory):
    with MEMORY_PATH.open("w", encoding="utf-8") as memory_file:
        json.dump(memory, memory_file, ensure_ascii=False, indent=2)
        memory_file.write("\n")


def infer_category(topic):
    text = str(topic or "").strip().lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
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


def build_session(topic, rating, category=None):
    return {
        "topic": topic,
        "category": (
            category.strip()
            if isinstance(category, str) and category.strip()
            else infer_category(topic)
        ),
        "rating": rating,
    }


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
