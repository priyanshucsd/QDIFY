"""QDIFY: generate and take a quiz from any PDF.  Run with: streamlit run app.py"""

import json
import os
from datetime import datetime

import streamlit as st

from backend import (
    DIFFICULTIES,
    MODEL,
    QUESTION_TYPES,
    QuizError,
    generate_quiz,
    grade,
    pdf_info,
)

st.set_page_config(page_title="QDIFY", page_icon="🧠", layout="centered")


# ----------------------------------------------------------------------------
# State
# ----------------------------------------------------------------------------
def init_state():
    defaults = {
        "quiz": None,       # list of question dicts
        "quiz_id": 0,       # bumps on every new quiz -> fresh widget keys
        "attempt": 0,       # bumps on every retake
        "submitted": False,
        "answers": [],
        "meta": {},
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def widget_key(i: int) -> str:
    return f"q{st.session_state.quiz_id}_{st.session_state.attempt}_{i}"


# ----------------------------------------------------------------------------
# Header + sidebar
# ----------------------------------------------------------------------------
def header():
    st.markdown(
        "<h1 style='text-align:center;margin-bottom:0'>🧠 QDIFY</h1>"
        "<p style='text-align:center;color:gray'>Turn any PDF into a quiz in seconds</p>",
        unsafe_allow_html=True,
    )


def sidebar():
    with st.sidebar:
        st.header("Quiz Settings")
        difficulty = st.selectbox("Difficulty", list(DIFFICULTIES), index=1)
        n = st.slider("Number of questions", 1, 15, 5)
        q_type = st.selectbox("Question type", QUESTION_TYPES)

        api_key = None
        if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
            st.divider()
            api_key = st.text_input("Gemini API key", type="password",
                                    help="Or set GEMINI_API_KEY in a .env file.")
        st.divider()
        st.caption(f"Model: {MODEL}")
    return difficulty, n, q_type, api_key


# ----------------------------------------------------------------------------
# Quiz taking
# ----------------------------------------------------------------------------
def render_quiz():
    quiz = st.session_state.quiz
    meta = st.session_state.meta
    st.subheader("Your Quiz")
    st.caption(f"{len(quiz)} questions · {meta['difficulty']} · {meta['type']} · from {meta['source']}")

    with st.form(f"form_{st.session_state.quiz_id}_{st.session_state.attempt}"):
        for i, q in enumerate(quiz):
            st.markdown(f"**Q{i + 1}. {q['question']}**")
            if "options" in q:
                st.radio("Select your answer", q["options"], index=None,
                         key=widget_key(i), label_visibility="collapsed")
            else:
                st.text_input("Your answer", key=widget_key(i),
                              placeholder="Type one word", label_visibility="collapsed")
            st.divider()

        if st.form_submit_button("Submit Quiz", type="primary", use_container_width=True):
            st.session_state.answers = [st.session_state.get(widget_key(i)) for i in range(len(quiz))]
            st.session_state.submitted = True
            st.rerun()


def results_text(quiz, answers, g, meta) -> str:
    lines = [
        f"QDIFY Results - {meta['source']}",
        f"Date: {datetime.now():%Y-%m-%d %H:%M}",
        f"Difficulty: {meta['difficulty']} | Type: {meta['type']}",
        f"Score: {g['score']}/{g['total']} ({g['percent']}%)",
        "",
    ]
    for i, (q, a, ok) in enumerate(zip(quiz, answers, g["results"]), 1):
        lines += [
            f"Q{i}. {q['question']}",
            f"   Your answer: {a or '(skipped)'}",
            f"   Correct answer: {q['answer']}  [{'correct' if ok else 'wrong'}]",
            f"   Explanation: {q['explanation']}",
            "",
        ]
    return "\n".join(lines)


def render_results():
    quiz = st.session_state.quiz
    answers = st.session_state.answers
    meta = st.session_state.meta
    g = grade(quiz, answers)

    st.subheader("Results")
    c1, c2 = st.columns(2)
    c1.metric("Score", f"{g['score']} / {g['total']}")
    c2.metric("Percentage", f"{g['percent']}%")
    st.progress(g["percent"] / 100)

    if g["percent"] >= 80:
        st.success("Strong result. You know this material well.")
    elif g["percent"] >= 50:
        st.info("Decent, but review the questions you missed below.")
    else:
        st.warning("Needs more revision. Go through the explanations below and retake.")

    st.markdown("#### Review")
    for i, (q, a, ok) in enumerate(zip(quiz, answers, g["results"]), 1):
        icon = "✅" if ok else "❌"
        with st.expander(f"{icon} Q{i}. {q['question']}", expanded=not ok):
            st.markdown(f"**Your answer:** {a or '_skipped_'}")
            st.markdown(f"**Correct answer:** {q['answer']}")
            if q["explanation"]:
                st.markdown(f"**Why:** {q['explanation']}")

    st.divider()
    b1, b2 = st.columns(2)
    if b1.button("🔁 Retake this quiz", use_container_width=True):
        st.session_state.attempt += 1
        st.session_state.submitted = False
        st.session_state.answers = []
        st.rerun()
    if b2.button("🗑️ Clear quiz", use_container_width=True):
        st.session_state.quiz = None
        st.session_state.submitted = False
        st.session_state.answers = []
        st.rerun()

    d1, d2 = st.columns(2)
    d1.download_button("⬇️ Results (.txt)", results_text(quiz, answers, g, meta),
                       file_name="qdify_results.txt", use_container_width=True)
    d2.download_button("⬇️ Quiz (.json)", json.dumps(quiz, indent=2),
                       file_name="qdify_quiz.json", mime="application/json",
                       use_container_width=True)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    init_state()
    header()
    difficulty, n, q_type, api_key = sidebar()

    up_file = st.file_uploader("Upload a PDF", type=["pdf"])

    if up_file is not None:
        try:
            info = pdf_info(up_file.getvalue())
            st.caption(f"📄 {up_file.name} · {info['pages']} pages")
            if not info["has_text"]:
                st.warning("This PDF looks scanned (little selectable text). "
                           "Quiz quality may be lower.")
        except QuizError as e:
            st.error(str(e))
            up_file = None

    if st.button("Generate Quiz", type="primary", use_container_width=True):
        if up_file is None:
            st.warning("Please upload a valid PDF first.")
        else:
            try:
                with st.spinner("Reading your PDF and writing questions..."):
                    quiz = generate_quiz(up_file.getvalue(), difficulty, n, q_type, api_key)
                st.session_state.update(
                    quiz=quiz,
                    quiz_id=st.session_state.quiz_id + 1,
                    attempt=0,
                    submitted=False,
                    answers=[],
                    meta={"difficulty": difficulty, "type": q_type, "source": up_file.name},
                )
                if len(quiz) < n:
                    st.warning(f"Only {len(quiz)} valid questions were generated (asked for {n}).")
            except QuizError as e:
                st.error(str(e))

    if st.session_state.quiz:
        st.divider()
        if st.session_state.submitted:
            render_results()
        else:
            render_quiz()


main()
