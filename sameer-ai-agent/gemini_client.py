"""Question and follow-up generation with Google Gemini."""

import json
import os
from threading import Lock

from google import genai
from google.genai import errors, types

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
TIMEOUT_MS = 20_000

CATEGORY_GUIDES = {
    "الذكريات": "مواقف ماضية جميلة عاشتها العائلة معًا، أو ذكريات من طفولة الوالدين.",
    "الامتنان": "ما نقدّره في يومنا وفي بعضنا، والنعم الصغيرة التي نلاحظها.",
    "الأحلام": "الطموحات والخيال والمستقبل، وما يتمنى كل فرد أن يفعله أو يصبح.",
    "الحكايات": "قصة قصيرة يبدأها سمير ويكملها أفراد العائلة، أو حكاية من تراثنا.",
    "القيم": "الصدق والرحمة والتعاون والاحترام من خلال مواقف يومية قريبة من الأطفال.",
}

SYSTEM_INSTRUCTION = """\
أنت "سمير"، روبوت صغير على مائدة العائلة العربية. دورك أن تطرح سؤالًا يفتح الحوار ثم تنسحب
وتترك العائلة تتحدث. لا تقود الحوار ولا تحتكره.

الأسلوب:
- عربية فصحى بسيطة ودافئة يفهمها طفل في الثامنة.
- جملة أو جملتان فقط، لا تزيد عن 25 كلمة، لأن الجهاز سينطقها بصوت عالٍ.
- سؤال مفتوح يستطيع كل فرد في العائلة، صغيرًا أو كبيرًا، أن يجيب عنه.
- بلا رموز تعبيرية، وبلا علامات تنصيص، وبلا شرح.

قيود المحتوى (العائلة فيها أطفال من 8 سنوات فأكثر):
- لا عنف ولا رعب ولا تفاصيل عن الموت أو المرض أو الحوادث.
- لا علاقات عاطفية، ولا سياسة، ولا خلافات مذهبية أو دينية، ولا مال أو مشاكل مادية.
- لا أسئلة عن شكل الجسم أو الوزن أو المقارنة بين الإخوة.
- لا أسئلة قد تُحرج أحدًا أو تكشف سرًّا عائليًا أو خلافًا بين الوالدين.
- لا تطلب معلومات شخصية (عنوان، مدرسة، أرقام، كلمات مرور).
- القيم الدينية تُطرح بشكل عام ولطيف فقط، دون فتاوى أو أحكام.
"""

_client = None
_client_lock = Lock()


class GeminiUnavailable(Exception):
    """Gemini is not configured, unreachable, or returned nothing usable."""


def _get_client():
    global _client
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiUnavailable("GEMINI_API_KEY is not configured.")
    with _client_lock:
        if _client is None:
            _client = genai.Client(
                api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS)
            )
        return _client


def _generate(prompt):
    try:
        response = _get_client().models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.9,
            ),
        )
    except errors.APIError as error:
        raise GeminiUnavailable(f"Gemini API error {error.code}: {error.message}") from error

    text = (response.text or "").strip().strip('"«»“”').strip()
    if not text:
        raise GeminiUnavailable("Gemini returned an empty response.")
    return text


def _json(data):
    return json.dumps(data, ensure_ascii=False)


def generate_question(category, ages, avoid_topics, occasion=None, tip=None):
    lines = [
        f"الفئة: {category} — {CATEGORY_GUIDES[category]}",
        f"أعمار الأطفال: {_json(ages)}" if ages else "أعمار الأطفال: من 8 إلى 14 سنة.",
    ]
    if occasion:
        lines.append(f"المناسبة أو الوقت: {occasion}")
    if tip:
        lines.append(f"ملاحظة من الجلسة السابقة: {tip}")
    lines.append(f"لا تكرر هذه الأسئلة السابقة ولا تقترب منها: {_json(avoid_topics)}")
    lines.append("اكتب سؤالًا واحدًا جديدًا يفتح حوارًا عائليًا في هذه الفئة. أعد السؤال فقط.")
    return _generate("\n".join(lines))


def generate_follow_up(category, question, previous_follow_ups):
    lines = [
        f"الفئة: {category} — {CATEGORY_GUIDES[category]}",
        f"السؤال الذي طرحه سمير في بداية الجلسة: {question}",
        "العائلة صامتة منذ فترة. لا تعرف ماذا قالوا، فلا تفترض إجاباتهم.",
    ]
    if previous_follow_ups:
        lines.append(f"أسئلة متابعة طرحتها من قبل (لا تكررها): {_json(previous_follow_ups)}")
    lines.append(
        "اكتب سؤال متابعة قصيرًا ولطيفًا يعيد الحوار، مثل دعوة فرد لم يتكلم بعد أو زاوية جديدة للسؤال نفسه. "
        "أعد السؤال فقط."
    )
    return _generate("\n".join(lines))


# Used only when Gemini is unavailable, so a tap on the device still starts a session.
FALLBACK_QUESTIONS = {
    "الذكريات": "ما أجمل يوم قضيناه معًا كعائلة، ولماذا بقي في ذاكرتك؟",
    "الامتنان": "ما الشيء الصغير الذي حدث اليوم وجعلك تبتسم؟",
    "الأحلام": "لو استطعت أن تتعلم أي مهارة في العالم غدًا، ماذا ستختار ولماذا؟",
    "الحكايات": "كان يا ما كان، طائر صغير وجد مفتاحًا ذهبيًا... ماذا فتح المفتاح؟ كل واحد يكمل جملة.",
    "القيم": "متى ساعدك أحد دون أن تطلب منه؟ وكيف شعرت؟",
}
FALLBACK_FOLLOW_UP = "من لم يشاركنا بعد؟ نحب أن نسمع رأيك أنت أيضًا."
