"""Question and follow-up generation with Google Gemini."""

import json
import os
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.parse import quote

from google import genai
from google.genai import errors, types

ROBOT_NAME = os.environ.get("ROBOT_NAME", "حوار").strip() or "حوار"
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
    "الحكايات": f"قصة قصيرة يبدأها {ROBOT_NAME} ويكملها أفراد العائلة، أو حكاية من تراثنا.",
    "القيم": "الصدق والرحمة والتعاون والاحترام من خلال مواقف يومية قريبة من الأطفال.",
}

SAFETY_RULES = """\
قيود المحتوى (العائلة فيها أطفال من 8 سنوات فأكثر):
- لا عنف ولا رعب ولا تفاصيل عن الموت أو المرض أو الحوادث.
- لا علاقات عاطفية، ولا سياسة، ولا خلافات مذهبية أو دينية، ولا مال أو مشاكل مادية.
- لا أسئلة عن شكل الجسم أو الوزن أو المقارنة بين الإخوة.
- لا أسئلة قد تُحرج أحدًا أو تكشف سرًّا عائليًا أو خلافًا بين الوالدين.
- لا تطلب معلومات شخصية (عنوان، مدرسة، أرقام، كلمات مرور).
- القيم الدينية تُطرح بشكل عام ولطيف فقط، دون فتاوى أو أحكام.
"""

SYSTEM_INSTRUCTION = f"""\
أنت "{ROBOT_NAME}"، روبوت صغير على مائدة العائلة العربية. دورك أن تطرح سؤالًا يفتح الحوار ثم تنسحب
وتترك العائلة تتحدث. لا تقود الحوار ولا تحتكره.

الأسلوب:
- عربية فصحى بسيطة ودافئة يفهمها طفل في الثامنة.
- جملة أو جملتان فقط، لا تزيد عن 25 كلمة، لأن الجهاز سينطقها بصوت عالٍ.
- سؤال مفتوح يستطيع كل فرد في العائلة، صغيرًا أو كبيرًا، أن يجيب عنه.
- بلا رموز تعبيرية، وبلا علامات تنصيص، وبلا شرح.

{SAFETY_RULES}"""

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


def _config(system_instruction, json_schema, thinking):
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.9,
        http_options=types.HttpOptions(timeout=TEXT_TIMEOUT_MS),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        **({"response_mime_type": "application/json", "response_json_schema": json_schema} if json_schema else {}),
        # Short spoken replies don't need deep reasoning; minimal thinking cuts seconds off each turn.
        **({"thinking_config": types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)} if thinking else {}),
    )


def _generate(contents, system_instruction=SYSTEM_INSTRUCTION, json_schema=None, budget_seconds=TEXT_BUDGET_SECONDS):
    """Generate text (or JSON), retrying a busy model once and then trying the fallback models."""
    config = _config(system_instruction, json_schema, thinking=True)
    attempts = [MODEL, MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]
    deadline = time.monotonic() + budget_seconds
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
            response = _get_client().models.generate_content(model=model, contents=contents, config=config)
        except errors.APIError as error:
            errors_seen.append(f"{model}: {error.code} {error.message}")
            if error.code == 400 and "think" in str(error.message).lower() and config.thinking_config:
                # This model doesn't take a thinking level: retry it without one.
                config = _config(system_instruction, json_schema, thinking=False)
                try:
                    response = _get_client().models.generate_content(model=model, contents=contents, config=config)
                except errors.APIError as retry_error:
                    errors_seen.append(f"{model} (no thinking): {retry_error.code} {retry_error.message}")
                    continue
                text = (response.text or "").strip()
                if not json_schema:
                    text = text.strip('"«»“”').strip()
                if text:
                    return text
                continue
            if error.code == 404:
                missing.add(model)
            if error.code in RETRYABLE_CODES or error.code == 404:
                continue  # busy, rate-limited, or unknown model: try the next one
            break  # bad key or bad request: other models won't help
        text = (response.text or "").strip()
        if not json_schema:
            text = text.strip('"«»“”').strip()
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
        f"السؤال الذي طرحه {ROBOT_NAME} في بداية الجلسة: {question}",
        "العائلة صامتة منذ فترة. لا تعرف ماذا قالوا، فلا تفترض إجاباتهم.",
    ]
    if previous_follow_ups:
        lines.append(f"أسئلة متابعة طرحتها من قبل (لا تكررها): {_json(previous_follow_ups)}")
    lines.append(
        "اكتب سؤال متابعة قصيرًا ولطيفًا يعيد الحوار، مثل دعوة فرد لم يتكلم بعد أو زاوية جديدة للسؤال نفسه. "
        "أعد السؤال فقط."
    )
    return _generate("\n".join(lines))


CONVERSE_INSTRUCTION = f"""\
أنت "{ROBOT_NAME}"، روبوت صغير ودود على مائدة العائلة العربية. طرحت على العائلة سؤالًا يفتح الحوار،
والآن تسمع مقطعًا صوتيًا قصيرًا قاله أحد أفراد العائلة. مهمتك أن تساعد الحوار لا أن تقوده.

قرر واحدًا من ثلاثة:
- "reply": إذا وُجّه الكلام إليك (نادوك باسمك، أو سألوك، أو طلبوا رأيك)، أو إذا أجاب أحدهم عن سؤالك
  وكان تعليق قصير منك سيشجع الآخرين على المشاركة. رُد بجملة أو جملتين قصيرتين لا تزيد عن 20 كلمة،
  بدفء وببساطة، وغالبًا اختم بدعوة فرد آخر للمشاركة أو بسؤال متابعة قريب من كلامهم.
- "listen": إذا كانت العائلة تتحدث فيما بينها والحوار ماشٍ، أو إذا كان المقطع غير واضح أو ضجيجًا.
  لا تقاطع حوارًا جيدًا. إذا كانت آخر ردودك قريبة جدًا من بعضها، فاختر "listen" ما لم يسألوك مباشرة.
- "wrap_up": إذا طلبوا إنهاء الجلسة أو ودّعوك. ودّعهم بجملة دافئة قصيرة واشكرهم.

قواعد الرد:
- عربية فصحى بسيطة ودافئة. يمكنك أن تفهم اللهجات، لكن رد بفصحى قريبة منها.
- رد على ما قالوه فعلًا، ولا تخترع تفاصيل لم تُذكر.
- إذا ذكر طفل أنه في خطر أو يتعرض للأذى، فشجّعه بلطف على إخبار أحد والديه أو شخص بالغ يثق به.
- بلا رموز تعبيرية وبلا علامات تنصيص.

{SAFETY_RULES}
في الحقل "heard" اكتب ملخصًا قصيرًا جدًا بالعربية لما قيل (لا يزيد عن 15 كلمة)، ليبقى سياق الجلسة.
"""

CONVERSE_SCHEMA = {
    "type": "object",
    "properties": {
        "heard": {"type": "string"},
        "action": {"type": "string", "enum": ["reply", "listen", "wrap_up"]},
        "reply": {"type": "string"},
    },
    "required": ["heard", "action", "reply"],
}


def converse(audio_wav, category, question, turns, budget_seconds=12):
    """Understand one spoken turn and decide how the robot responds.

    turns: earlier turns in this session, [{"heard": str, "reply": str}], kept in memory only.
    Returns {"heard": str, "action": "reply"|"listen"|"wrap_up", "reply": str}.
    """
    context = [
        f"فئة الجلسة: {category} — {CATEGORY_GUIDES.get(category, '')}",
        f"سؤالك في بداية الجلسة: {question}",
    ]
    if turns:
        context.append("ما دار حتى الآن (الأقدم أولًا):")
        for turn in turns[-8:]:
            context.append(f"- العائلة: {turn['heard']}" + (f" | أنت: {turn['reply']}" if turn.get("reply") else ""))
    context.append("استمع إلى المقطع الصوتي التالي وقرر.")

    raw = _generate(
        ["\n".join(context), types.Part.from_bytes(data=audio_wav, mime_type="audio/wav")],
        system_instruction=CONVERSE_INSTRUCTION,
        json_schema=CONVERSE_SCHEMA,
        budget_seconds=budget_seconds,
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GeminiUnavailable(f"Gemini returned invalid JSON: {raw[:120]}") from error

    action = data.get("action") if data.get("action") in ("reply", "listen", "wrap_up") else "listen"
    reply = str(data.get("reply") or "").strip().strip('"«»“”').strip()
    if action != "listen" and not reply:
        action = "listen"
    return {"heard": str(data.get("heard") or "").strip()[:200], "action": action, "reply": reply[:400]}


INSIGHT_INSTRUCTION = f"""\
أنت "{ROBOT_NAME}"، روبوت الحوار العائلي. تكتب للعائلة ملاحظة أسبوعية قصيرة في لوحتها.
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


def synthesize_speech(text, timeout_ms=TIMEOUT_MS):
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
                http_options=types.HttpOptions(timeout=timeout_ms),
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


# Live voice conversation: the robot streams audio straight to Gemini over a WebSocket, so replies
# start within about a second instead of waiting for upload, generation and speech one after another.
LIVE_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.8-live")
LIVE_VOICE = os.environ.get("GEMINI_LIVE_VOICE", TTS_VOICE)
LIVE_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained"
)
LIVE_TOKEN_MINUTES = 35  # a conversation can run 30 minutes
LIVE_TOKEN_USES = 6  # the first connection plus reconnections (Gemini closes a connection every ~10 minutes)
LIVE_SILENCE_MS = 800  # how long a pause ends someone's turn
END_TOOL = "end_conversation"


def live_instruction(category, ages, avoid_topics, tip=None, opening_question=None, family_name=None):
    """System instruction for a live spoken conversation with the family."""
    opening = (
        f"ابدأ بترحيب قصير جدًا ثم اطرح هذا السؤال كما هو: {opening_question}"
        if opening_question
        else f"ابدأ بترحيب قصير جدًا ثم اطرح سؤالًا مفتوحًا واحدًا جديدًا من فئة {category}: {CATEGORY_GUIDES[category]}"
    )
    lines = [
        f"""أنت "{ROBOT_NAME}"، روبوت صغير ودود على مائدة العائلة، في محادثة صوتية مباشرة معهم.
هدفك أن تفتح حوارًا ممتعًا بين أفراد العائلة وتبقيه حيًّا، لا أن تحاضر.

كيف تتحدث:
- {opening}
- بعدها تحاور بشكل طبيعي وسريع مثل صديق: اسمع، علّق بجملة دافئة قصيرة، واسأل سؤال متابعة.
- ادعُ من لم يتكلم بعد أن يشارك، ووزّع الكلام بينهم بلطف.
- إذا سألك أحدهم سؤالًا فأجبه مباشرة وباختصار، ثم أعد الحوار إليهم.
- إذا كانوا يتحاورون فيما بينهم فاكتفِ بتعليق قصير جدًا أو كلمة تشجيع، ولا تقاطعهم.
- كل رد جملة أو جملتان فقط، لأنك تتكلم بصوت مسموع.
- عربية بسيطة دافئة يفهمها طفل في الثامنة؛ إن تكلمت العائلة بلهجتها فجارِها.
- إذا قالوا إنهم انتهوا أو ودّعوك، فودّعهم بجملة قصيرة ثم استدعِ الأداة {END_TOOL}.
- الرسائل النصية التي تبدأ بـ [تنبيه] تأتي من جهازك وليست من العائلة: نفّذها بصوتك دون أن تذكرها.
- لا تطلب معلومات شخصية ولا تكرر ما قيل خارج هذه المحادثة.""",
        SAFETY_RULES,
        f"أعمار الأطفال: {_json(ages)}" if ages else "أعمار الأطفال: من 8 إلى 14 سنة.",
    ]
    if family_name:
        lines.append(f"اسم العائلة: {family_name}")
    if tip:
        lines.append(f"ملاحظة من الجلسة السابقة: {tip}")
    if avoid_topics and not opening_question:
        lines.append(f"لا تكرر هذه الأسئلة السابقة: {_json(avoid_topics)}")
    return "\n\n".join(lines)


def _live_config(instruction):
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=instruction,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=LIVE_VOICE))
        ),
        tools=[
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=END_TOOL, description="أنهِ الجلسة بعد أن تودّع العائلة."
                    )
                ]
            )
        ],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(silence_duration_ms=LIVE_SILENCE_MS)
        ),
    )


def live_setup(instruction):
    """The setup message the device sends first on the WebSocket (camelCase, as on the wire)."""
    return {
        "setup": {
            "model": f"models/{LIVE_MODEL}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": LIVE_VOICE}}},
            },
            "systemInstruction": {"parts": [{"text": instruction}]},
            "tools": [
                {"functionDeclarations": [{"name": END_TOOL, "description": "أنهِ الجلسة بعد أن تودّع العائلة."}]}
            ],
            "outputAudioTranscription": {},
            "realtimeInputConfig": {"automaticActivityDetection": {"silenceDurationMs": LIVE_SILENCE_MS}},
            "sessionResumption": {},
        }
    }


def start_live(instruction):
    """Create a short-lived Gemini token for one conversation, locked to this instruction.

    The device never sees the API key: the token works only for this Live setup, a few connections,
    and LIVE_TOKEN_MINUTES. Returns {"ws_url", "token", "setup"}.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiUnavailable("GEMINI_API_KEY is not configured.")
    client = genai.Client(
        api_key=api_key, http_options=types.HttpOptions(api_version="v1alpha", timeout=TEXT_TIMEOUT_MS)
    )
    expires = datetime.now(timezone.utc) + timedelta(minutes=LIVE_TOKEN_MINUTES)
    try:
        token = client.auth_tokens.create(
            config=types.CreateAuthTokenConfig(
                uses=LIVE_TOKEN_USES,
                expire_time=expires,
                new_session_expire_time=expires,
                live_connect_constraints=types.LiveConnectConstraints(
                    model=LIVE_MODEL, config=_live_config(instruction)
                ),
                # Lock the fields set above; the device may still add a session-resumption handle.
                lock_additional_fields=[],
            )
        )
    except errors.APIError as error:
        raise GeminiUnavailable(f"Could not create a Live token: {error.code} {error.message}") from error
    if not token.name:
        raise GeminiUnavailable("Gemini returned an empty Live token.")
    return {
        "ws_url": f"{LIVE_WS_URL}?access_token={quote(token.name, safe='')}",
        "token": token.name,
        "setup": live_setup(instruction),
    }


# Used only when Gemini is unavailable, so a tap on the device still starts a session.
FALLBACK_QUESTIONS = {
    "الذكريات": "ما أجمل يوم قضيناه معًا كعائلة، ولماذا بقي في ذاكرتك؟",
    "الامتنان": "ما الشيء الصغير الذي حدث اليوم وجعلك تبتسم؟",
    "الأحلام": "لو استطعت أن تتعلم أي مهارة في العالم غدًا، ماذا ستختار ولماذا؟",
    "الحكايات": "كان يا ما كان، طائر صغير وجد مفتاحًا ذهبيًا... ماذا فتح المفتاح؟ كل واحد يكمل جملة.",
    "القيم": "متى ساعدك أحد دون أن تطلب منه؟ وكيف شعرت؟",
}
FALLBACK_FOLLOW_UP = "من لم يشاركنا بعد؟ نحب أن نسمع رأيك أنت أيضًا."
