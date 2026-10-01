"""When Sameer steps in during a session, and how a finished session is evaluated.

Both work only on numbers the device measures (silence, talk time per anonymous
speaker). Spoken turns are handled separately by /converse.
"""

import math

SILENCE_BEFORE_FOLLOW_UP = 30  # seconds of silence before Hiwar considers stepping in
MIN_GAP_BETWEEN_FOLLOW_UPS = 60  # seconds, so Hiwar doesn't crowd the conversation
MAX_FOLLOW_UPS = 6
SILENCE_BEFORE_WRAP_UP = 90  # after the last follow-up, only a long silence ends the session
MAX_SESSION_SECONDS = 45 * 60
BALANCED = 0.85  # normalized entropy of talk shares; ~72/28 for two speakers, ~60/25/15 for three
CHECK_AGAIN_SECONDS = 10

WRAP_UP_TEXT = "كانت جلسة جميلة! شكرًا لكم، ونلتقي في حوار قادم."


def decide_intervention(elapsed_seconds, silence_seconds, talking, follow_ups_so_far, last_follow_up_at):
    """Return "wait", "follow_up" or "wrap_up".

    Sameer asks and steps back: while the family is talking it always waits, and it only
    speaks up after a long silence, a limited number of times per session.
    """
    if elapsed_seconds >= MAX_SESSION_SECONDS:
        return "wrap_up"
    if talking or silence_seconds < SILENCE_BEFORE_FOLLOW_UP:
        return "wait"
    if follow_ups_so_far >= MAX_FOLLOW_UPS:
        return "wrap_up" if silence_seconds >= SILENCE_BEFORE_WRAP_UP else "wait"
    if last_follow_up_at is not None and elapsed_seconds - last_follow_up_at < MIN_GAP_BETWEEN_FOLLOW_UPS:
        return "wait"
    return "follow_up"


def evaluate(duration_seconds, silence_seconds, speakers, follow_ups_used):
    """Score a finished session 1-3 from participation numbers and suggest improvements.

    speakers: list of {"talk_seconds": float, "turns": int} for anonymous speakers.
    """
    talk_times = [max(0.0, float(s.get("talk_seconds", 0))) for s in speakers]
    turns = sum(max(0, int(s.get("turns", 0))) for s in speakers)
    total_talk = sum(talk_times)
    duration = max(1.0, float(duration_seconds))

    if silence_seconds is not None:
        talk_ratio = max(0.0, min(1.0, 1 - float(silence_seconds) / duration))
    else:
        talk_ratio = min(1.0, total_talk / duration)

    active = [t for t in talk_times if t > 0]
    balance = None
    dominant_share = None
    if len(active) >= 2:
        shares = [t / total_talk for t in active]
        entropy = -sum(p * math.log(p) for p in shares)
        balance = entropy / math.log(len(active))
        dominant_share = max(shares)

    points = 0
    points += talk_ratio >= 0.5
    points += duration >= 5 * 60
    # With no speaker data the device can't tell who spoke, so balance isn't held against the family.
    points += balance >= BALANCED if balance is not None else not active
    score = 1 if points <= 1 else points

    suggestions = []
    if balance is not None and balance < BALANCED:
        suggestions.append("صوت واحد كان هو الأعلى. في الجلسة القادمة جرّبوا أن يجيب كل فرد بدوره.")
    if len(active) == 1:
        suggestions.append("تحدث فرد واحد تقريبًا. اختاروا سؤالًا يدعو الجميع للمشاركة.")
    if talk_ratio < 0.5:
        suggestions.append("كان الصمت طويلًا. جربوا فئة الحكايات أو الأحلام فهي أسهل للبدء.")
    if duration < 5 * 60:
        suggestions.append("كانت الجلسة قصيرة. خصصوا عشر دقائق بعد الوجبة بلا أجهزة.")
    if follow_ups_used >= MAX_FOLLOW_UPS:
        suggestions.append("احتاج سمير للتدخل كثيرًا. ابدأوا بسؤال أقرب لاهتمامات الأطفال.")
    if not suggestions:
        suggestions.append("جلسة متوازنة وحيوية! حافظوا على الوقت نفسه في الأسبوع القادم.")

    return {
        "score": score,
        "duration_seconds": round(duration),
        "talk_ratio": round(talk_ratio, 2),
        "speakers": len(active),
        "balance": None if balance is None else round(balance, 2),
        "dominant_share": None if dominant_share is None else round(dominant_share, 2),
        "turns": turns,
        "average_turn_seconds": round(total_talk / turns, 1) if turns else None,
        "follow_ups_used": follow_ups_used,
        "suggestions": suggestions,
    }
