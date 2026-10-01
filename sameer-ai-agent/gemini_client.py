"""Question and follow-up generation with Google Gemini."""

import json
import os
import re
import time
from collections import OrderedDict
from threading import Lock

from google import genai
from google.genai import errors, types

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")
TTS_VOICE = os.environ.get("GEMINI_TTS_VOICE", "Puck")
TTS_SAMPLE_RATE = 24_000  # matches the StackChan speaker, so the device plays it as-is
# Tried in order when the main model is busy (503) or rate-limited (429).
FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get("GEMINI_FALLBACK_MODELS", "gemini-flash-lite-latest,gemini-flash-latest").split(",")
    if m.strip()
]
TIMEOUT_MS = 20_000
TEXT_TIMEOUT_MS = 10_000
TEXT_BUDGET_SECONDS = 24  # stay under the server's 30 s request timeout
RETRYABLE_CODES = {429, 500, 503, 504}

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


def _generate(prompt, system_instruction=SYSTEM_INSTRUCTION):
    """Generate text, retrying a busy model once and then trying the fallback models."""
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.9,
        http_options=types.HttpOptions(timeout=TEXT_TIMEOUT_MS),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    attempts = [MODEL, MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]
    deadline = time.monotonic() + TEXT_BUDGET_SECONDS
    errors_seen = []
    missing = set()

    for index, model in enumerate(attempts):
        if model in missing:
            continue
        if time.monotonic() + TEXT_TIMEOUT_MS / 1000 > deadline:
            break
        if index == 1:
            time.sleep(1)  # brief pause before retrying the main model
        try:
            response = _get_client().models.generate_content(model=model, contents=prompt, config=config)
        except errors.APIError as error:
            errors_seen.append(f"{model}: {error.code} {error.message}")
            if error.code == 404:
                missing.add(model)
            if error.code in RETRYABLE_CODES or error.code == 404:
                continue  # busy, rate-limited, or unknown model: try the next one
            break  # bad key or bad request: other models won't help
        text = (response.text or "").strip().strip('"«»“”').strip()
        if text:
            return text
        errors_seen.append(f"{model}: empty response")

    raise GeminiUnavailable("Gemini unavailable (" + "; ".join(errors_seen or ["timed out"]) + ")")


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


INSIGHT_INSTRUCTION = """\
أنت "سمير"، روبوت الحوار العائلي. تكتب للعائلة ملاحظة أسبوعية قصيرة في لوحتها.
- جملة أو جملتان، لا تزيد عن 30 كلمة، بعربية فصحى دافئة، بصيغة المتكلم ("لاحظت أن...").
- اعتمد فقط على الأرقام المعطاة، ولا تذكر رقمًا غير موجود فيها، ولا تخمّن ما قالته العائلة.
- الأرقام عن نشاط الحوار (عدد الجلسات، مدتها، عدد المشاركين) وليست تقييمًا نفسيًا لأحد.
- كن إيجابيًا ومشجعًا، ولا تلُم أحدًا، ولا تستخدم كلمات مثل مراقبة أو تتبع أو رصد.
- بلا رموز تعبيرية وبلا علامات تنصيص.
"""


def generate_insight(facts):
    prompt = (
        "أرقام آخر 30 يومًا (weekend = الجمعة والسبت، weekdays = باقي الأيام، "
        "engagement_by_category = نسبة التفاعل لكل فئة من 100):\n"
        f"{_json(facts)}\n"
        "اكتب ملاحظة واحدة لافتة ومفيدة للعائلة من هذه الأرقام."
    )
    return _generate(prompt, INSIGHT_INSTRUCTION)


_speech_cache = OrderedDict()
_speech_lock = Lock()
SPEECH_CACHE_SIZE = 32


def synthesize_speech(text):
    """Arabic speech for `text` as raw 16-bit mono PCM. Returns (pcm_bytes, sample_rate)."""
    with _speech_lock:
        if text in _speech_cache:
            _speech_cache.move_to_end(text)
            return _speech_cache[text]

    try:
        response = _get_client().models.generate_content(
            model=TTS_MODEL,
            contents=f"Say warmly and calmly, like a friendly companion at a family dinner table:\n{text}",
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=TTS_VOICE)
                    )
                ),
            ),
        )
    except errors.APIError as error:
        raise GeminiUnavailable(f"Gemini TTS error {error.code}: {error.message}") from error

    parts = (response.candidates or [None])[0]
    parts = parts.content.parts if parts and parts.content else []
    blob = next((p.inline_data for p in parts if p.inline_data and p.inline_data.data), None)
    if blob is None:
        raise GeminiUnavailable("Gemini TTS returned no audio.")

    rate = re.search(r"rate=(\d+)", blob.mime_type or "")
    result = (blob.data, int(rate.group(1)) if rate else TTS_SAMPLE_RATE)
    with _speech_lock:
        _speech_cache[text] = result
        while len(_speech_cache) > SPEECH_CACHE_SIZE:
            _speech_cache.popitem(last=False)
    return result


# Used only when Gemini is unavailable, so a tap on the device still starts a session.
FALLBACK_QUESTIONS = {
    "الذكريات": "ما أجمل يوم قضيناه معًا كعائلة، ولماذا بقي في ذاكرتك؟",
    "الامتنان": "ما الشيء الصغير الذي حدث اليوم وجعلك تبتسم؟",
    "الأحلام": "لو استطعت أن تتعلم أي مهارة في العالم غدًا، ماذا ستختار ولماذا؟",
    "الحكايات": "كان يا ما كان، طائر صغير وجد مفتاحًا ذهبيًا... ماذا فتح المفتاح؟ كل واحد يكمل جملة.",
    "القيم": "متى ساعدك أحد دون أن تطلب منه؟ وكيف شعرت؟",
}
FALLBACK_FOLLOW_UP = "من لم يشاركنا بعد؟ نحب أن نسمع رأيك أنت أيضًا."
