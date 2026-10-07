# QDIFY - PDF Quiz Generator

Upload a PDF, pick difficulty / number / type of questions, and QDIFY generates a quiz
using the Google Gemini API. Take the quiz in the browser, get a score, and review
every answer with an explanation.

## Features
- PDF upload with validation (size limit, corrupt / password-protected / scanned warnings)
- 3 question types: MCQ, True/False, One Word
- 4 difficulty levels: Easy, Medium, High, Expert
- 1-15 questions per quiz
- Structured JSON output from Gemini (schema-enforced) + validation + one automatic retry
- MCQ options are shuffled so the answer is not always in the same position
- Submit, auto-grading, score, per-question review with explanations
- Retake the same quiz, or generate a new one
- Download results (.txt) and the quiz (.json)

## Project structure
```
qdify/
├── app.py              # Streamlit UI (upload, settings, quiz, results)
├── backend.py          # PDF checks, Gemini call, validation, grading
├── requirements.txt
└── README.md
```

## Setup
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create a file named `.env` in the project folder with your Gemini API key
(free key: https://aistudio.google.com/apikey):
```
GEMINI_API_KEY=your_api_key_here
```

Then start the app:
```bash
streamlit run app.py
```
If you skip the `.env` file, the app asks for the key in the sidebar.

## Configuration
| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_API_KEY` | Gemini API key | none (required) |
| `QDIFY_MODEL` | Gemini model name | `gemini-3.1-flash-lite` |

If you get a "model not found" error, set `QDIFY_MODEL` in `.env` to a model listed
in the current Gemini API docs.

## How it works
1. `app.py` reads the uploaded PDF bytes and calls `generate_quiz()`.
2. `backend.generate_quiz()` validates the PDF (`pypdf`), sends it to Gemini with a
   JSON schema for the chosen question type, then validates and cleans the result.
3. The quiz is stored in `st.session_state`; answers are collected in a form.
4. `backend.grade()` compares answers (case and punctuation insensitive) and the
   results screen shows the score and explanations.

## Limitations
- PDFs over 20 MB are rejected (sent inline to the API).
- Scanned PDFs work but may give weaker questions.
- Quiz history is not saved between sessions (possible future work: SQLite).