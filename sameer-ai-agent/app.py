import json
import os
from pathlib import Path
from threading import Lock

from anthropic import Anthropic
from flask import Flask, jsonify, request


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


@app.get("/")
def index():
    return "sameer-ai-agent is running."


@app.get("/get_question")
def get_question():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY is not configured."}), 500

    try:
        with MEMORY_LOCK:
            memory = load_memory()
            recent_sessions = memory["sessions"][-10:]

        prompt = f"""
Suggest one new Arabic family-discussion question for children aged 8-14.
Use the recent sessions below to avoid repeating topics and to choose a
thoughtful, age-appropriate discussion prompt. The question should be clear,
warm, and suitable for a family conversation.

Recent sessions:
{json.dumps(recent_sessions, ensure_ascii=False, indent=2)}

Return only the Arabic question, with no explanation and no quotation marks.
""".strip()

        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        question = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        ).strip()

        if not question:
            return jsonify({"error": "Anthropic returned an empty question."}), 502

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

    required_fields = ("topic", "category", "rating")
    missing_fields = [field for field in required_fields if field not in payload]
    if missing_fields:
        return jsonify(
            {"error": f"Missing required fields: {', '.join(missing_fields)}."}
        ), 400

    session = {field: payload[field] for field in required_fields}
    try:
        with MEMORY_LOCK:
            memory = load_memory()
            memory["sessions"].append(session)
            save_memory(memory)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return jsonify({"error": str(error)}), 500

    return jsonify({"status": "saved"})


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8080,
        debug=False,
    )