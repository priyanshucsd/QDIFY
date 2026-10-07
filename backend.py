"""QDIFY backend: turns a PDF into a quiz using the Gemini API.

Kept free of any Streamlit code so it can be tested on its own.
"""

from __future__ import annotations

import io
import json
import os
import random
import re
import string

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel
from pypdf import PdfReader

load_dotenv()

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
# Override with QDIFY_MODEL in your .env if this model name changes.
MODEL = os.getenv("QDIFY_MODEL", "gemini-3.1-flash-lite")
MAX_PDF_MB = 20  # PDFs are sent inline, so keep them reasonably small

MCQ = "MCQ"
TRUE_FALSE = "True/False"
ONE_WORD = "One Word Questions"
QUESTION_TYPES = [MCQ, TRUE_FALSE, ONE_WORD]

DIFFICULTIES = {
    "Easy": "direct recall of facts and definitions stated in the document",
    "Medium": "understanding and applying concepts explained in the document",
    "High": "multi-step reasoning that connects several ideas from the document",
    "Expert": "tricky, analytical questions that need deep understanding; "
    "plausible distractors that differ only subtly",
}


class QuizError(Exception):
    """Any error that should be shown to the user as a plain message."""


# ----------------------------------------------------------------------------
# Output schemas (Gemini returns JSON matching these)
# ----------------------------------------------------------------------------
class MCQItem(BaseModel):
    question: str
    options: list[str]
    answer: str
    explanation: str


class TrueFalseItem(BaseModel):
    question: str
    answer: str
    explanation: str


class OneWordItem(BaseModel):
    question: str
    answer: str
    explanation: str


_SCHEMAS = {MCQ: MCQItem, TRUE_FALSE: TrueFalseItem, ONE_WORD: OneWordItem}

_TYPE_RULES = {
    MCQ: (
        "multiple choice questions. Give exactly 4 distinct options. "
        "'answer' must be the exact text of the correct option."
    ),
    TRUE_FALSE: (
        "True/False statements. 'question' is a statement to judge. "
        "'answer' must be exactly 'True' or 'False'. Mix true and false statements."
    ),
    ONE_WORD: (
        "one-word-answer questions. 'answer' must be a single word "
        "(or a single number) with no extra text."
    ),
}


# ----------------------------------------------------------------------------
# PDF helpers
# ----------------------------------------------------------------------------
def pdf_info(data: bytes) -> dict:
    """Validate a PDF and return basic info. Raises QuizError if unusable."""
    if len(data) > MAX_PDF_MB * 1024 * 1024:
        raise QuizError(f"PDF is larger than {MAX_PDF_MB} MB. Please upload a smaller file.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise QuizError("This PDF is password protected. Remove the password and try again.")
        pages = len(reader.pages)
        sample = "".join((p.extract_text() or "") for p in reader.pages[:10])
    except QuizError:
        raise
    except Exception:
        raise QuizError("Could not read this file. Is it a valid PDF?")

    if pages == 0:
        raise QuizError("This PDF has no pages.")
    return {
        "pages": pages,
        "has_text": len(sample.strip()) > 50,  # False usually means a scanned PDF
    }


# ----------------------------------------------------------------------------
# Parsing + validation
# ----------------------------------------------------------------------------
def _parse_json(text: str) -> list:
    """Parse model output, tolerating ```json fences."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of questions.")
    return data


def _validate(items: list, q_type: str, n: int) -> list[dict]:
    """Clean raw questions, drop malformed ones, normalise answers."""
    quiz: list[dict] = []
    seen: set[str] = set()

    for raw in items:
        if not isinstance(raw, dict):
            continue
        question = str(raw.get("question", "")).strip()
        answer = str(raw.get("answer", "")).strip()
        explanation = str(raw.get("explanation", "")).strip()
        if not question or not answer or question.lower() in seen:
            continue

        item = {"question": question, "answer": answer, "explanation": explanation}

        if q_type == MCQ:
            opts = [str(o).strip() for o in raw.get("options", []) if str(o).strip()]
            opts = list(dict.fromkeys(opts))  # de-duplicate, keep order
            match = next((o for o in opts if o.lower() == answer.lower()), None)
            if len(opts) < 2 or match is None:
                continue
            random.shuffle(opts)  # models love putting the answer in the same slot
            item["options"] = opts
            item["answer"] = match

        elif q_type == TRUE_FALSE:
            answer = answer.capitalize()
            if answer not in ("True", "False"):
                continue
            item["options"] = ["True", "False"]
            item["answer"] = answer

        seen.add(question.lower())
        quiz.append(item)

    return quiz[:n]


# ----------------------------------------------------------------------------
# Generation
# ----------------------------------------------------------------------------
def _make_client(api_key: str | None = None) -> genai.Client:
    key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise QuizError(
            "No Gemini API key found. Set GEMINI_API_KEY in your .env file "
            "or paste it in the sidebar."
        )
    return genai.Client(api_key=key)


def _build_prompt(difficulty: str, n: int, q_type: str) -> str:
    return f"""You are an exam setter. Using ONLY the attached PDF, write exactly {n} {_TYPE_RULES[q_type]}

Difficulty: {difficulty} - {DIFFICULTIES[difficulty]}.

Rules:
- Every question must be answerable from the PDF content alone.
- Cover different parts of the document; no duplicate or near-duplicate questions.
- Do not mention "the PDF", "the text", "the passage" or "the document" in the question wording.
- 'explanation' is one or two sentences saying why the answer is correct.
"""


def generate_quiz(
    pdf_bytes: bytes,
    difficulty: str,
    n: int,
    q_type: str,
    api_key: str | None = None,
) -> list[dict]:
    """Generate a quiz from PDF bytes. Returns a list of question dicts.

    Each dict has: question, answer, explanation, and (for MCQ / True-False)
    options. May return fewer than n questions if the model produced some
    invalid ones; raises QuizError if nothing usable comes back.
    """
    if q_type not in _SCHEMAS:
        raise QuizError(f"Unknown question type: {q_type}")
    if difficulty not in DIFFICULTIES:
        raise QuizError(f"Unknown difficulty: {difficulty}")
    n = int(n)

    pdf_info(pdf_bytes)  # raises QuizError on bad files
    client = _make_client(api_key)

    contents = [
        types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
        _build_prompt(difficulty, n, q_type),
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=list[_SCHEMAS[q_type]],
        temperature=0.7,
    )

    last_error = "unknown error"
    for _ in range(2):  # one retry for flaky output
        try:
            resp = client.models.generate_content(model=MODEL, contents=contents, config=config)
            quiz = _validate(_parse_json(resp.text), q_type, n)
            if quiz:
                return quiz
            last_error = "the model returned no valid questions"
        except QuizError:
            raise
        except (ValueError, json.JSONDecodeError) as e:
            last_error = f"could not parse the model's output ({e})"
        except Exception as e:  # network, quota, bad key, bad model name...
            raise QuizError(f"Gemini API error: {e}")

    raise QuizError(f"Quiz generation failed: {last_error}. Please try again.")


# ----------------------------------------------------------------------------
# Grading
# ----------------------------------------------------------------------------
def _norm(s: str) -> str:
    return " ".join(str(s).lower().translate(str.maketrans("", "", string.punctuation)).split())


def is_correct(question: dict, given: str | None) -> bool:
    if not given:
        return False
    return _norm(given) == _norm(question["answer"])


def grade(quiz: list[dict], answers: list[str | None]) -> dict:
    results = [is_correct(q, a) for q, a in zip(quiz, answers)]
    score = sum(results)
    total = len(quiz)
    return {
        "score": score,
        "total": total,
        "percent": round(100 * score / total) if total else 0,
        "results": results,
    }
