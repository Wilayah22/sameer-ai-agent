"""Everything the family dashboard shows, computed from memory.json.

All figures describe conversation activity (sessions, duration, who took part), never
what was said. When the family has no completed sessions yet, the dashboard is built
from a clearly-labelled demo family instead.
"""

import random
from datetime import datetime, timedelta

from memory_store import CATEGORIES, effective_rating, session_category

WINDOW_DAYS = 30
WEEKDAYS = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")
WEEKEND = (4, 5)  # Friday, Saturday


def _started(session, tz):
    value = session.get("started_at")
    if not isinstance(value, str):
        return None
    try:
        started = datetime.fromisoformat(value)
    except ValueError:
        return None
    return started.replace(tzinfo=tz) if started.tzinfo is None else started.astimezone(tz)


def _duration_minutes(session, tz):
    evaluation = session.get("evaluation") or {}
    if evaluation.get("duration_seconds"):
        return evaluation["duration_seconds"] / 60
    started, rated = _started(session, tz), session.get("rated_at")
    if started and isinstance(rated, str):
        try:
            minutes = (datetime.fromisoformat(rated).astimezone(tz) - started).total_seconds() / 60
        except ValueError:
            return None
        if 0 < minutes <= 120:
            return minutes
    return None


def _participants(session):
    evaluation = session.get("evaluation") or {}
    members = evaluation.get("members_spoke")
    if members:
        return len(members)
    return evaluation.get("speakers") or None


def _questions(session):
    """Things the robot said to keep the conversation going: the question, follow-ups, and replies."""
    return 1 + len(session.get("follow_ups", [])) + session.get("replies", 0)


def _engagement(session):
    """0-1: the family's rating, blended with how much of the session was conversation."""
    rating = effective_rating(session)
    if rating is None:
        return None
    talk_ratio = (session.get("evaluation") or {}).get("talk_ratio")
    if talk_ratio is None:
        return rating / 3
    return 0.6 * rating / 3 + 0.4 * talk_ratio


def bond_index(sessions):
    """Same formula as the original dashboard: sessions up to 20 (40) + rating of 3 (45) + categories of 5 (15)."""
    ratings = [effective_rating(s) for s in sessions]
    ratings = [r for r in ratings if r is not None]
    if not ratings:
        return 0
    categories = {session_category(s) for s in sessions} & set(CATEGORIES)
    score = (
        min(len(ratings) / 20, 1) * 40
        + min(sum(ratings) / len(ratings) / 3, 1) * 45
        + min(len(categories) / 5, 1) * 15
    )
    return round(score)


def bond_label(value):
    if value >= 80:
        return "تفاعل عائلي ممتاز"
    if value >= 60:
        return "تفاعل عائلي جيد"
    if value >= 40:
        return "تفاعل عائلي في نمو"
    if value > 0:
        return "بداية رحلة الحوار"
    return "ابدأوا أول جلسة"


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _pct_change(now, before):
    if not now or not before:
        return None
    return round((now - before) / before * 100)


def build_dashboard(memory, now, tz):
    timed = [(s, _started(s, tz)) for s in memory["sessions"]]
    timed = [(s, t) for s, t in timed if t is not None]
    completed = [(s, t) for s, t in timed if effective_rating(s) is not None]

    window_start = now - timedelta(days=WINDOW_DAYS)
    previous_start = now - timedelta(days=2 * WINDOW_DAYS)
    current = [s for s, t in completed if t > window_start]
    previous = [s for s, t in completed if previous_start < t <= window_start]

    bond = bond_index(current)
    bond_before = bond_index(previous)

    # Trend: the index over the 30 days ending on each of the last 30 days.
    trend = []
    for offset in range(WINDOW_DAYS - 1, -1, -1):
        day_end = (now - timedelta(days=offset)).replace(hour=23, minute=59, second=59)
        in_window = [s for s, t in completed if day_end - timedelta(days=WINDOW_DAYS) < t <= day_end]
        sessions_that_day = sum(1 for _, t in completed if t.date() == day_end.date())
        trend.append(
            {"date": day_end.date().isoformat(), "value": bond_index(in_window), "sessions": sessions_that_day}
        )

    durations = [_duration_minutes(s, tz) for s in current]
    durations_before = [_duration_minutes(s, tz) for s in previous]
    avg_duration = _mean(durations)
    questions = sum(_questions(s) for s in current)
    questions_before = sum(_questions(s) for s in previous)

    members = memory["family"].get("members") or []
    labelled = [s for s in current if (s.get("evaluation") or {}).get("members_spoke")]
    spoke = {}
    for session in labelled:
        for member in session["evaluation"]["members_spoke"]:
            spoke[member] = spoke.get(member, 0) + 1
    roles = [m["role"] for m in members] + [r for r in spoke if r not in {m["role"] for m in members}]
    participation = [
        {"role": role, "pct": round(spoke.get(role, 0) / len(labelled) * 100) if labelled else None}
        for role in roles
    ]
    participants = len([p for p in participation if p["pct"]]) or max(
        (_participants(s) or 0 for s in current), default=0
    )

    topics = []
    for category in CATEGORIES:
        values = [_engagement(s) for s in current if session_category(s) == category]
        mean = _mean(values)
        topics.append(
            {
                "category": category,
                "pct": None if mean is None else round(mean * 100),
                "sessions": len([v for v in values if v is not None]),
            }
        )
    topics.sort(key=lambda t: -1 if t["pct"] is None else t["pct"], reverse=True)

    recent = []
    for session, started in sorted(timed, key=lambda pair: pair[1], reverse=True)[:20]:
        duration = _duration_minutes(session, tz)
        recent.append(
            {
                "id": session.get("id"),
                "weekday": WEEKDAYS[started.weekday()],
                "date": started.date().isoformat(),
                "category": session_category(session),
                "topic": session.get("topic", ""),
                "duration_minutes": None if duration is None else round(duration),
                "participants": _participants(session),
                "rating": effective_rating(session),
                "status": "completed" if effective_rating(session) is not None else "awaiting_rating",
            }
        )

    return {
        "family": {"name": memory["family"].get("name") or "عائلتي", "members": members},
        "bond": {
            "value": bond,
            "label": bond_label(bond),
            "change_pct": _pct_change(bond, bond_before),
        },
        "kpis": {
            "sessions": len(current),
            "sessions_change": len(current) - len(previous),
            "avg_duration_minutes": None if avg_duration is None else round(avg_duration),
            "duration_change_pct": _pct_change(avg_duration, _mean(durations_before)),
            "participants": participants,
            "members_total": len(members),
            "questions": questions,
            "questions_change": questions - questions_before,
        },
        "trend": trend,
        "topics": topics,
        "participation": participation,
        "recent_sessions": recent,
        "facts": insight_facts(current, tz),
    }


def insight_facts(sessions, tz):
    """Numbers Sameer's weekly note is written from."""

    def group(items):
        return {
            "sessions": len(items),
            "avg_minutes": _round(_mean([_duration_minutes(s, tz) for s in items])),
            "avg_participants": _round(_mean([_participants(s) for s in items])),
        }

    weekend = [s for s in sessions if _started(s, tz).weekday() in WEEKEND]
    weekdays = [s for s in sessions if _started(s, tz).weekday() not in WEEKEND]
    evening = [s for s in sessions if _started(s, tz).hour >= 18]

    by_category = {}
    for category in CATEGORIES:
        values = [_engagement(s) for s in sessions if session_category(s) == category]
        mean = _mean(values)
        if mean is not None:
            by_category[category] = round(mean * 100)

    return {
        "sessions": len(sessions),
        "weekend": group(weekend),
        "weekdays": group(weekdays),
        "evening_share": round(len(evening) / len(sessions) * 100) if sessions else None,
        "engagement_by_category": by_category,
    }


def _round(value):
    return None if value is None else round(value, 1)


def rule_based_insight(facts):
    weekend, weekdays = facts["weekend"], facts["weekdays"]
    if weekend["sessions"] >= 2 and weekdays["sessions"] >= 2:
        longer = (weekend["avg_minutes"] or 0) > (weekdays["avg_minutes"] or 0) * 1.15
        more = (weekend["avg_participants"] or 0) > (weekdays["avg_participants"] or 0)
        if longer and more:
            return "لاحظت أن جلساتكم في نهاية الأسبوع تستمر لفترة أطول ويشارك فيها أفراد أكثر."
        if longer:
            return "لاحظت أن جلساتكم في نهاية الأسبوع تستمر لفترة أطول من أيام الدراسة."
    if facts["engagement_by_category"]:
        best = max(facts["engagement_by_category"], key=facts["engagement_by_category"].get)
        return f"أكثر جلساتكم حيوية كانت في فئة {best}. سأقترح منها أكثر دون أن أنسى باقي الفئات."
    if facts["sessions"]:
        return "بداية جميلة! كل جلسة جديدة تساعدني على اقتراح أسئلة تناسب عائلتكم أكثر."
    return "المسوا حوار على المائدة لتبدأ أول جلسة عائلية."


def demo_memory(now, seed=1):
    """A realistic month for a demo family, used only until the real family completes a session."""
    rng = random.Random(seed)
    members = [
        {"role": "الأب", "age": 42},
        {"role": "الأم", "age": 38},
        {"role": "الابن", "age": 12},
        {"role": "الابنة", "age": 9},
        {"role": "الجدة", "age": 68},
    ]
    presence = {"الأب": 0.86, "الأم": 0.82, "الابن": 0.71, "الابنة": 0.68, "الجدة": 0.63}
    topics = {
        "الذكريات": ["ما أجمل ذكرى من طفولتك؟", "ما أجمل رحلة قمنا بها معًا؟"],
        "الأحلام": ["ما أحلامنا للمستقبل؟", "لو استطعت أن تبني أي شيء، ماذا ستبني؟"],
        "الامتنان": ["ما الشيء الذي نفتخر به في عائلتنا؟", "ما الموقف الذي جعلك ممتنًا هذا الأسبوع؟"],
        "القيم": ["متى ساعدك أحد دون أن تطلب؟", "ما معنى أن نكون صادقين حتى في الأشياء الصغيرة؟"],
        "الحكايات": ["نكمل معًا حكاية الطائر والمفتاح الذهبي", "حكاية الجد الذي زرع نخلة"],
    }
    plan = [
        (-55, "القيم", 2, 14), (-50, "الحكايات", 2, 16), (-46, "الامتنان", 2, 18), (-41, "الذكريات", 3, 22),
        (-37, "الأحلام", 2, 17), (-34, "القيم", 2, 15), (-32, "الحكايات", 2, 19), (-31, "الامتنان", 2, 20),
        (-28, "الحكايات", 2, 19), (-26, "الامتنان", 2, 20), (-23, "الذكريات", 3, 30), (-19, "الأحلام", 2, 24),
        (-16, "الحكايات", 2, 20), (-14, "القيم", 2, 22), (-12, "الذكريات", 3, 29), (-9, "القيم", 2, 23),
        (-7, "الأحلام", 2, 26), (-5, "الامتنان", 2, 25), (-3, "الأحلام", 3, 21), (-1, "الذكريات", 3, 28),
    ]
    sessions = []
    for index, (days_ago, category, rating, minutes) in enumerate(plan):
        started = (now + timedelta(days=days_ago)).replace(hour=19 + index % 3, minute=15, second=0, microsecond=0)
        weekend = started.weekday() in WEEKEND
        boost = 0.12 if weekend else 0
        spoke = [m["role"] for m in members if rng.random() < presence[m["role"]] + boost]
        spoke = spoke or ["الأب", "الأم"]
        sessions.append(
            {
                "id": f"demo{index:02d}",
                "category": category,
                "topic": topics[category][index % 2],
                "started_at": started.isoformat(timespec="seconds"),
                "follow_ups": [{"at": 300, "text": ""}] * (index % 3),
                "rating": rating,
                "evaluation": {
                    "score": rating,
                    "duration_seconds": (minutes + (4 if weekend else 0)) * 60,
                    "talk_ratio": round(0.62 + rng.random() * 0.3, 2),
                    "speakers": len(spoke),
                    "members_spoke": spoke,
                },
            }
        )
    return {"sessions": sessions, "family": {"name": "عائلة السالم", "members": members, "ages": []}}
