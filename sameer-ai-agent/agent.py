"""Sameer's Claude-powered agent: a family-conversation companion with tools over memory.json."""

import json
import uuid
from collections import OrderedDict
from threading import Lock

import anthropic
from anthropic import beta_tool

from memory_store import (
    MEMORY_LOCK,
    build_session,
    compute_personalization_stats,
    load_memory,
    save_memory,
)

MODEL = "claude-opus-5-5"
FALLBACK_BETAS = ["server-side-fallback-2026-07-01"]
MAX_CONVERSATIONS = 200
MAX_USER_TURNS = 40
REFUSAL_REPLY = "آسف، ما أقدر أساعد في هذا الطلب. خلونا نختار موضوعًا ثانيًا للحوار العائلي."

SYSTEM_PROMPT = """\
أنت "سمير"، روبوت حوار عائلي صغير يجلس مع العائلات العربية على المائدة.
مهمتك أن تفتح للعائلة حوارًا دافئًا وهادفًا بين الوالدين والأطفال (من 8 إلى 14 سنة).

طريقتك:
- تكلّم بعربية بسيطة ودافئة تناسب الأطفال، وجمل قصيرة. استخدم لهجة المتحدث إن كانت واضحة.
- اقترح سؤالًا واحدًا في كل مرة، ثم استمع وعلّق على الإجابات بلطف، واطرح سؤالًا متابعًا يشرك فردًا آخر.
- شجّع الاستماع وتبادل الأدوار، ولا تحكم على إجابة أحد.
- قبل أن تقترح موضوعًا جديدًا، استخدم أداة get_family_profile لتعرف أفراد العائلة وأعمارهم والفئات التي أحبوها، وتجنّب تكرار آخر المواضيع.
- حين يذكر أحد اسمه وعمره أو اهتماماته، احفظها بأداة remember_family_member.
- حين تنتهي جلسة حوار، اسأل العائلة عن تقييمها (1 = عادية، 2 = جيدة، 3 = رائعة) ثم احفظها بأداة save_session.

الأمان:
- كل المحتوى مناسب لعمر الأطفال. تجنّب المواضيع المخيفة أو غير اللائقة، وأعد الحوار بلطف إلى موضوع عائلي.
- لا تطلب معلومات شخصية حساسة (عنوان، مدرسة بالاسم، أرقام هواتف، صور).
- إن ذكر طفل أنه في خطر أو يتعرض للأذى، شجّعه بلطف على إخبار أحد والديه أو شخص بالغ يثق به فورًا.
- أنت لا تحل محل الوالدين؛ دورك أن تبدأ الحوار وتتركهم يتحدثون.
"""


def _json(data):
    return json.dumps(data, ensure_ascii=False)


@beta_tool
def get_family_profile() -> str:
    """Get the family's members, rating statistics per discussion category, and the last 5 topics discussed.

    Call this before suggesting a new discussion topic so the suggestion fits the
    children's ages, leans toward the categories the family rated highest, and
    does not repeat a recent topic.
    """
    with MEMORY_LOCK:
        memory = load_memory()
        stats = compute_personalization_stats(memory)
    return _json({"family_members": memory["family"], **stats})


@beta_tool
def get_recent_sessions(limit: int = 10) -> str:
    """Get the most recent saved discussion sessions (topic, category, rating), newest last.

    Args:
        limit: How many sessions to return, between 1 and 50.
    """
    limit = max(1, min(int(limit), 50))
    with MEMORY_LOCK:
        sessions = load_memory()["sessions"][-limit:]
    return _json(sessions)


@beta_tool
def remember_family_member(
    name: str, age: int | None = None, interests: str = "", notes: str = ""
) -> str:
    """Save or update a family member so Sameer can tailor future questions to them.

    If a member with the same name exists, the provided fields are updated.

    Args:
        name: The person's first name, as they introduced themselves.
        age: Their age in years, if known.
        interests: Short comma-separated interests, e.g. "كرة القدم، الرسم".
        notes: Any other short, non-sensitive note (e.g. "الأخ الأكبر", "يحب الأسئلة الخيالية").
    """
    name = name.strip()
    if not name:
        return _json({"error": "name is required"})

    with MEMORY_LOCK:
        memory = load_memory()
        member = next((m for m in memory["family"] if m.get("name") == name), None)
        if member is None:
            member = {"name": name}
            memory["family"].append(member)
        if age is not None:
            member["age"] = age
        if interests.strip():
            member["interests"] = interests.strip()
        if notes.strip():
            member["notes"] = notes.strip()
        save_memory(memory)
    return _json({"status": "saved", "member": member})


@beta_tool
def save_session(topic: str, rating: int, category: str = "") -> str:
    """Save a finished discussion session with the family's rating.

    Only call this after the family has actually given a rating.

    Args:
        topic: The discussion question or topic that was talked about.
        rating: The family's rating: 1 (ordinary), 2 (good), or 3 (great).
        category: One of "دينية", "مهارات تواصل", "اجتماعية", "حياتية", "عام". Leave empty to infer it.
    """
    if rating not in (1, 2, 3):
        return _json({"error": "rating must be 1, 2, or 3"})

    session = build_session(topic, rating, category)
    with MEMORY_LOCK:
        memory = load_memory()
        memory["sessions"].append(session)
        save_memory(memory)
    return _json({"status": "saved", "session": session})


TOOLS = [get_family_profile, get_recent_sessions, remember_family_member, save_session]


def _reply_text(message):
    if message.stop_reason == "refusal":
        return REFUSAL_REPLY
    return "\n".join(b.text for b in message.content if b.type == "text").strip()


class SameerAgent:
    def __init__(self, client=None):
        self._client = client
        self._conversations = OrderedDict()
        self._store_lock = Lock()

    @property
    def client(self):
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def _get_conversation(self, conversation_id):
        with self._store_lock:
            if conversation_id and conversation_id in self._conversations:
                self._conversations.move_to_end(conversation_id)
                return conversation_id, self._conversations[conversation_id]

            conversation_id = uuid.uuid4().hex
            conversation = {"messages": [], "user_turns": 0, "lock": Lock()}
            self._conversations[conversation_id] = conversation
            while len(self._conversations) > MAX_CONVERSATIONS:
                self._conversations.popitem(last=False)
            return conversation_id, conversation

    def chat(self, user_message, conversation_id=None):
        """Send one user message and run the tool loop. Returns (conversation_id, reply)."""
        conversation_id, conversation = self._get_conversation(conversation_id)

        with conversation["lock"]:
            if conversation["user_turns"] >= MAX_USER_TURNS:
                raise ConversationFullError(conversation_id)

            # Work on a copy so a failed request leaves the stored history untouched.
            messages = list(conversation["messages"])
            messages.append({"role": "user", "content": user_message})

            runner = self.client.beta.messages.tool_runner(
                model=MODEL,
                max_tokens=16000,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=TOOLS,
                output_config={"effort": "medium"},
                betas=FALLBACK_BETAS,
                fallbacks="default",
                messages=messages,
            )

            last = None
            for message in runner:
                last = message
                # Mirror the full content (incl. thinking blocks) so history stays append-only.
                messages.append({"role": "assistant", "content": message.content})
                tool_response = runner.generate_tool_call_response()
                if tool_response is not None:
                    messages.append(tool_response)

            conversation["messages"] = messages
            conversation["user_turns"] += 1

        return conversation_id, _reply_text(last) if last else ""

    def suggest_question(self):
        """One-shot: suggest a single new family-discussion question based on memory."""
        with MEMORY_LOCK:
            memory = load_memory()
            stats = compute_personalization_stats(memory)
            recent_sessions = memory["sessions"][-10:]

        prompt = f"""اقترح سؤال نقاش عائلي واحدًا جديدًا بالعربية لأطفال من 8 إلى 14 سنة.

أفراد العائلة:
{_json(memory["family"]) if memory["family"] else "غير معروفين بعد"}

الفئات الأعلى تقييمًا عند العائلة: {_json(stats["highest_rated_categories"] or ["عام"])}
متوسط التقييم لكل فئة: {_json(stats["average_rating_per_category"])}
آخر المواضيع (لا تكررها): {_json(stats["recent_topics"])}
آخر الجلسات: {_json(recent_sessions)}

مِل إلى الفئات الأعلى تقييمًا مع الحفاظ على سؤال جديد ومدروس، واضح ودافئ.
أعد السؤال فقط، دون شرح ودون علامات تنصيص."""

        response = self.client.beta.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "low"},
            betas=FALLBACK_BETAS,
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            return ""
        return _reply_text(response)


class ConversationFullError(Exception):
    def __init__(self, conversation_id):
        super().__init__(f"Conversation {conversation_id} reached {MAX_USER_TURNS} turns.")
        self.conversation_id = conversation_id
