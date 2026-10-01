"""Sameer's memory: short text summaries of sessions (category, question, rating) in memory.json.

No audio and no conversation transcripts are ever stored here.
"""

import json
import os
import random
import uuid
from collections import defaultdict
from pathlib import Path
from threading import Lock

MEMORY_PATH = Path(__file__).with_name("memory.json")
MEMORY_LOCK = Lock()

# On hosts with an ephemeral disk (Render's free tier wipes files on every deploy), set
# DATABASE_URL to a Postgres database and the same JSON document is kept there instead.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
EMPTY_MEMORY = {"sessions": [], "family": {"name": "", "members": [], "ages": []}}


def _db_connect():
    import psycopg

    conn = psycopg.connect(DATABASE_URL, connect_timeout=10, autocommit=True)
    conn.execute("CREATE TABLE IF NOT EXISTS sameer_memory (id INTEGER PRIMARY KEY, data JSONB NOT NULL)")
    return conn


def _read_raw():
    if DATABASE_URL:
        with _db_connect() as conn:
            row = conn.execute("SELECT data FROM sameer_memory WHERE id = 1").fetchone()
        return json.loads(json.dumps(EMPTY_MEMORY)) if row is None else row[0]
    if not MEMORY_PATH.exists():
        save_memory(json.loads(json.dumps(EMPTY_MEMORY)))
    with MEMORY_PATH.open("r", encoding="utf-8") as memory_file:
        return json.load(memory_file)

CATEGORIES = ("الذكريات", "الامتنان", "الأحلام", "الحكايات", "القيم")

CATEGORY_KEYWORDS = {
    "الذكريات": ("ذكرى", "ذكريات", "تذكر", "طفولة", "زمان", "أول مرة", "memory", "remember"),
    "الامتنان": ("امتنان", "شكر", "نشكر", "ممتن", "نعمة", "نعم", "أقدر", "grateful", "thank"),
    "الأحلام": ("حلم", "أحلام", "تحلم", "مستقبل", "لو كنت", "لو صرت", "طموح", "dream", "future"),
    "الحكايات": ("حكاية", "حكايات", "قصة", "قصص", "حكي", "story", "tale"),
    "القيم": ("صدق", "أمانة", "رحمة", "تعاون", "احترام", "مساعدة", "عدل", "قيمة", "value", "kind"),
}

# Last categories used get damped so Sameer keeps rotating (most recent first).
RECENCY_DAMPING = (0.1, 0.4, 0.7)
UNUSED_BONUS = 1.3
PRIOR_MEAN = 2.0
PRIOR_WEIGHT = 2.0

OCCASION_BOOSTS = {
    "رمضان": {"القيم": 1.8, "الامتنان": 1.5},
    "العيد": {"الامتنان": 1.8, "الذكريات": 1.5},
    "عيد": {"الامتنان": 1.8, "الذكريات": 1.5},
    "بداية الدراسة": {"الأحلام": 1.8},
    "نهاية الدراسة": {"الذكريات": 1.5, "الامتنان": 1.5},
    "إجازة": {"الحكايات": 1.5, "الأحلام": 1.4},
    "سفر": {"الذكريات": 1.5, "الحكايات": 1.4},
    "عيد ميلاد": {"الذكريات": 1.6, "الأحلام": 1.6},
}


def load_memory():
    memory = _read_raw()

    if not isinstance(memory, dict) or not isinstance(memory.get("sessions"), list):
        raise ValueError('memory.json must contain a "sessions" list.')
    if not isinstance(memory.get("family"), dict):
        memory["family"] = {"ages": []}
    for key in ("members", "ages"):
        if not isinstance(memory["family"].get(key), list):
            memory["family"][key] = []
    # One-to-one conversations with a single family member, kept apart from family sessions.
    if not isinstance(memory.get("personal_sessions"), list):
        memory["personal_sessions"] = []

    return memory


def save_memory(memory):
    if DATABASE_URL:
        from psycopg.types.json import Jsonb

        with _db_connect() as conn:
            conn.execute(
                "INSERT INTO sameer_memory (id, data) VALUES (1, %s) "
                "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                (Jsonb(memory),),
            )
        return
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


def parse_rating(value):
    """Return the rating as an int 1-3, or None if it isn't one."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer() and 1 <= number <= 3:
        return int(number)
    return None


def effective_rating(session):
    """The family's manual rating when given, otherwise the automatic evaluation score."""
    rating = parse_rating(session.get("rating"))
    if rating is not None:
        return rating
    evaluation = session.get("evaluation")
    if isinstance(evaluation, dict):
        return parse_rating(evaluation.get("score"))
    return None


def find_session(memory, session_id):
    return next((s for s in memory["sessions"] if s.get("id") == session_id), None)


# General subjects of a personal conversation: the dashboard shows these, never what was said.
PERSONAL_TOPICS = (
    "المدرسة",
    "الأصدقاء",
    "الهوايات",
    "الرياضة",
    "المشاعر",
    "التعلّم",
    "القصص والألعاب",
    "الأحلام والأهداف",
    "العائلة",
    "يومي",
    "أخرى",
)
GUEST = "ضيف"
# How a member introduced by voice is related to the family.
RELATIONS = ("الأب", "الأم", "الابن", "الابنة", "الجد", "الجدة", "الأخ", "الأخت", "العم", "العمة", "الخال", "الخالة", "أخرى")


def member_label(member):
    """How a family member is shown and addressed: their name if given, else their role."""
    return (member.get("name") or member.get("role") or "").strip()


def member_labels(memory):
    return [label for label in (member_label(m) for m in memory["family"]["members"]) if label]


def find_personal_session(memory, session_id):
    return next((s for s in memory.get("personal_sessions", []) if s.get("id") == session_id), None)


def new_session(category, question, now):
    return {
        "id": uuid.uuid4().hex[:12],
        "category": category,
        "topic": question,
        "started_at": now.isoformat(timespec="seconds"),
        "follow_ups": [],
    }


def family_ages(memory):
    ages = [
        m["age"] for m in memory["family"]["members"] if isinstance(m.get("age"), int) and m["age"] < 18
    ]
    return ages or memory["family"]["ages"]


def build_session(topic, rating, category=None, now=None):
    return {
        "id": uuid.uuid4().hex[:12],
        **({"started_at": now.isoformat(timespec="seconds")} if now else {}),
        "topic": topic,
        "category": (
            category.strip()
            if isinstance(category, str) and category.strip()
            else infer_category(topic)
        ),
        "rating": rating,
    }


def category_weights(memory, now, occasion=None):
    """Weight each category by family preference, recent use, and time/occasion.

    Returns {category: (weight, [reasons])}.
    """
    ratings = defaultdict(list)
    for session in memory["sessions"]:
        rating = effective_rating(session)
        if rating is not None:
            ratings[session_category(session)].append(rating)

    recent = [
        s.get("category") for s in reversed(memory["sessions"]) if s.get("category") in CATEGORIES
    ]
    recent_unique = list(dict.fromkeys(recent))[: len(RECENCY_DAMPING)]

    boosts = defaultdict(lambda: 1.0)
    occasion_text = (occasion or "").strip()
    for key, per_category in OCCASION_BOOSTS.items():
        if key in occasion_text:
            for category, boost in per_category.items():
                boosts[category] = max(boosts[category], boost)
    if now.hour >= 19 or now.hour < 4:
        boosts["الحكايات"] = max(boosts["الحكايات"], 1.4)
    if now.weekday() in (4, 5):  # Friday, Saturday
        boosts["الذكريات"] = max(boosts["الذكريات"], 1.3)

    weights = {}
    for category in CATEGORIES:
        reasons = []
        values = ratings.get(category, [])
        preference = (sum(values) + PRIOR_MEAN * PRIOR_WEIGHT) / (len(values) + PRIOR_WEIGHT)
        weight = preference - 0.5
        if values:
            reasons.append(f"تقييم العائلة {preference:.1f}")
        if category in recent_unique:
            weight *= RECENCY_DAMPING[recent_unique.index(category)]
            reasons.append("استُخدمت مؤخرًا")
        elif category not in recent:
            weight *= UNUSED_BONUS
            reasons.append("لم تُجرَّب بعد")
        if boosts[category] > 1.0:
            weight *= boosts[category]
            reasons.append("تناسب الوقت أو المناسبة")
        weights[category] = (weight, reasons)
    return weights


def choose_category(memory, now, occasion=None, rng=random):
    weights = category_weights(memory, now, occasion)
    categories = list(weights)
    chosen = rng.choices(categories, weights=[weights[c][0] for c in categories], k=1)[0]
    return chosen, weights[chosen][1]


def recent_topics(sessions, limit=5):
    topics = []
    for session in reversed(sessions):
        topic = session.get("topic")
        if isinstance(topic, str) and topic.strip() and topic.strip() not in topics:
            topics.append(topic.strip())
            if len(topics) == limit:
                break
    return topics


def last_evaluation_tip(memory):
    for session in reversed(memory["sessions"]):
        evaluation = session.get("evaluation")
        if isinstance(evaluation, dict) and evaluation.get("suggestions"):
            return evaluation["suggestions"][0]
    return None


def compute_personalization_stats(memory):
    """Stats over completed sessions (those with a manual rating or an automatic score)."""
    category_ratings = defaultdict(list)
    all_ratings = []
    completed = []

    for session in memory["sessions"]:
        rating = effective_rating(session)
        if rating is None:
            continue
        completed.append(session)
        category_ratings[session_category(session)].append(rating)
        all_ratings.append(rating)

    average_rating_per_category = {
        category: round(sum(values) / len(values), 2)
        for category, values in category_ratings.items()
    }
    highest_average = max(average_rating_per_category.values(), default=None)

    return {
        "total_sessions": len(completed),
        "average_rating_overall": (
            round(sum(all_ratings) / len(all_ratings), 2) if all_ratings else 0.0
        ),
        "average_rating_per_category": average_rating_per_category,
        "highest_rated_categories": [
            category
            for category, average in average_rating_per_category.items()
            if average == highest_average
        ],
        "recent_topics": recent_topics(completed),
    }

