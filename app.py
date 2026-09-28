"""
JOB COPILOT  —  v2 (shareable)
------------------------------
Paste a job description. The AI tells you:
  * how well it fits YOU (score 0-100 and a verdict: Apply / Maybe / Skip)
  * what you match and what you're missing
  * which CV bullets to use (built only from your own CV, no invented numbers)
  * a referral note for the hiring manager and one for the recruiter

What changed in v2, so you can share one link with many people:
  * Each visitor uploads THEIR OWN CV (PDF, Word or text).
  * Each visitor pastes THEIR OWN free Gemini key. Your key is never used by others.
  * Everything a visitor enters stays private to their own browser tab.
    Nothing is saved to files, so no one can see anyone else's CV.

How to run it:   streamlit run app.py
(See README.md for the full step-by-step setup.)

Read this file top to bottom. Each numbered section does ONE job.
"""

# ─────────────────────────────────────────────────────────────
# 1. IMPORTS — borrowing tools other people already wrote
# ─────────────────────────────────────────────────────────────
import io                        # lets us treat an uploaded file like a file on disk
from datetime import date        # stamp today's date on each tracker row

import pandas as pd              # tables: used for the tracker
import streamlit as st           # turns this script into a web page
from docx import Document        # reads Word (.docx) files
from google import genai         # Google's Gemini AI
from google.genai import types
from pydantic import BaseModel, Field   # describes the exact shape of the AI's answer
from pypdf import PdfReader      # reads PDF files


# ─────────────────────────────────────────────────────────────
# 2. SETTINGS — things you might want to change later
# ─────────────────────────────────────────────────────────────
# Gemini models to choose from in the sidebar. "Flash-Lite" is the lightest and
# the one most likely to be on the free tier. If one gives a quota error, try another.
MODELS = ["gemini-3.5-flash-lite", "gemini-flash-latest", "gemini-3.8-flash"]

TRACKER_COLUMNS = ["date", "company", "role", "location", "score", "verdict", "status", "link"]


# ─────────────────────────────────────────────────────────────
# 3. THE ANSWER'S SHAPE — "structured output"
#    Instead of free text, we ask the AI to fill in this form.
#    That's what makes the result reliable enough to save in a table.
# ─────────────────────────────────────────────────────────────
class FitReport(BaseModel):
    company: str = Field(description="Company name, or 'Unknown'")
    role_title: str
    location: str = Field(description="City / remote / hybrid, or 'Not stated'")
    seniority: str = Field(description="e.g. Director, VP, Group PM, Senior PM")
    fit_score: int = Field(description="0-100: how well the candidate fits, judged against their CV AND their criteria")
    verdict: str = Field(description="Exactly one of: Apply, Maybe, Skip")
    why: list[str] = Field(description="3-5 short reasons behind the score")
    matched: list[str] = Field(description="Requirements from the JD the candidate clearly meets, each with the CV evidence")
    gaps: list[str] = Field(description="Requirements the CV does not show, most important first")
    cv_bullets: list[str] = Field(description="4-6 CV bullets to lead with for THIS job, rewritten from the candidate's own CV only")
    note_hiring_manager: str = Field(description="LinkedIn note to the hiring manager, under 90 words")
    note_recruiter: str = Field(description="LinkedIn note to the recruiter / TA partner, under 70 words")


# ─────────────────────────────────────────────────────────────
# 4. THE INSTRUCTIONS WE GIVE THE AI (the "prompt")
#    This is where most of the product thinking lives. Edit freely.
# ─────────────────────────────────────────────────────────────
SYSTEM_INSTRUCTIONS = """
You are a sharp, honest career advisor for an experienced professional.
You compare ONE job description against the candidate's CV and their personal job criteria.

Rules:
- Be honest. A low score with clear reasons is more useful than a flattering one.
- The candidate's criteria (level, city, domain, etc.) count as much as skills.
  If the job breaks a hard criterion, the verdict is Skip even if skills match.
- CV bullets must use ONLY facts and numbers that appear in the CV.
  Never invent a metric. If a bullet would be stronger with a number the CV doesn't have,
  write [add metric] in its place.
- Outreach notes: warm, specific to this role, no clichés like "I hope this finds you well",
  end with a clear, small ask (a referral or a 15-minute call). Use [Name] for the person's name.
"""


def build_prompt(cv: str, criteria: str, jd: str) -> str:
    """Glue the three pieces of text into one message for the AI."""
    return f"""
=== CANDIDATE CV ===
{cv}

=== CANDIDATE'S JOB CRITERIA ===
{criteria or "None given."}

=== JOB DESCRIPTION ===
{jd}
"""


# ─────────────────────────────────────────────────────────────
# 5. TALKING TO GEMINI
# ─────────────────────────────────────────────────────────────
def analyse_job(api_key: str, model: str, cv: str, criteria: str, jd: str) -> FitReport:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=build_prompt(cv, criteria, jd),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTIONS,
            response_mime_type="application/json",  # "answer in JSON..."
            response_schema=FitReport,              # "...shaped exactly like FitReport"
            temperature=0.3,                        # low = more consistent answers
        ),
    )
    # The SDK usually turns the JSON into a FitReport for us. If not, we do it ourselves.
    if isinstance(response.parsed, FitReport):
        return response.parsed
    return FitReport.model_validate_json(response.text)


# ─────────────────────────────────────────────────────────────
# 6. READING AN UPLOADED CV (PDF, Word or text)
# ─────────────────────────────────────────────────────────────
def read_cv_file(uploaded) -> str:
    name = uploaded.name.lower()
    data = uploaded.getvalue()
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
    if name.endswith(".docx"):
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs).strip()
    return data.decode("utf-8", errors="ignore").strip()   # .txt


# ─────────────────────────────────────────────────────────────
# 7. EACH VISITOR'S PRIVATE MEMORY ("session state")
#    st.session_state is a small notebook Streamlit keeps for EACH browser tab.
#    Visitor A never sees visitor B's notebook. It's wiped when the tab closes,
#    which is why the tracker has a Download button.
# ─────────────────────────────────────────────────────────────
for key, start_value in {"cv": "", "criteria": "", "tracker": [], "cv_file_id": None}.items():
    if key not in st.session_state:
        st.session_state[key] = start_value


def add_to_tracker(report: FitReport, link: str) -> None:
    st.session_state.tracker.append({
        "date": date.today().isoformat(),
        "company": report.company,
        "role": report.role_title,
        "location": report.location,
        "score": report.fit_score,
        "verdict": report.verdict,
        "status": "Analysed",
        "link": link,
    })


def key_from_secrets() -> str:
    """Only for running on YOUR laptop: reads your key from .streamlit/secrets.toml.
    On the shared online version, never add your key to the website's secrets —
    then this finds nothing and each visitor pastes their own."""
    try:
        return st.secrets["GEMINI_API_KEY"]
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────
# 8. THE WEB PAGE — everything below draws what you see
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="Job Copilot", page_icon="🎯", layout="wide")
st.title("🎯 Job Copilot")
st.caption("Your CV and results stay private to this browser tab. Nothing is stored after you close it.")

with st.sidebar:
    st.header("Your Gemini key")
    api_key = st.text_input("Paste your own Gemini API key", value=key_from_secrets(), type="password")
    st.markdown("No key yet? It's free and takes 2 minutes: go to "
                "[aistudio.google.com/apikey](https://aistudio.google.com/apikey), "
                "sign in with Google, click **Create API key**, copy it here.")
    model = st.selectbox("Model", MODELS)
    st.caption("Quota error? Pick another model or wait a minute.")

tab_job, tab_profile, tab_tracker = st.tabs(["Analyse a job", "My CV", "Tracker"])

# ---- Tab: My CV -----------------------------------------------------------
with tab_profile:
    uploaded = st.file_uploader("Upload your CV", type=["pdf", "docx", "txt"])
    # Read a newly uploaded file once, then let the visitor edit the text freely
    if uploaded is not None and uploaded.file_id != st.session_state.cv_file_id:
        text = read_cv_file(uploaded)
        st.session_state.cv_file_id = uploaded.file_id
        if len(text) < 100:
            st.warning("Found almost no text in that file. If it's a scanned PDF, paste your CV text below instead.")
        else:
            st.session_state.cv = text
            st.success("CV loaded. Check the text below and fix anything that came out jumbled.")

    st.text_area("CV text (or paste it here)", key="cv", height=350)
    st.text_area(
        "What you're looking for",
        key="criteria",
        height=160,
        placeholder="Example:\n- Level: Director / VP / Head of Product\n"
                    "- Cities: Bangalore first, then Hyderabad, Delhi NCR\n"
                    "- Strong domains: payments, fintech, consumer apps\n"
                    "- Skip: roles with no product ownership",
    )

# ---- Tab: Analyse a job ---------------------------------------------------
with tab_job:
    link = st.text_input("Job link (optional, just for your tracker)")
    jd_text = st.text_area("Paste the full job description", height=300)

    if st.button("Analyse", type="primary"):
        cv = st.session_state.cv
        # Check we have everything before calling the AI
        if not api_key:
            st.error("Paste your Gemini API key in the sidebar first (it's free).")
        elif len(cv.strip()) < 100:
            st.error("Go to the 'My CV' tab and upload or paste your CV first.")
        elif len(jd_text.strip()) < 200:
            st.error("That job description looks too short. Paste the whole thing.")
        else:
            with st.spinner("Reading the JD against your CV..."):
                try:
                    report = analyse_job(api_key, model, cv, st.session_state.criteria, jd_text)
                except Exception as err:
                    st.error(f"The AI call failed: {err}")
                    st.stop()

            add_to_tracker(report, link)

            # Show the result
            st.subheader(f"{report.role_title} — {report.company}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Fit score", f"{report.fit_score}/100")
            c2.metric("Verdict", report.verdict)
            c3.metric("Location", report.location)

            st.markdown("**Why**")
            for reason in report.why:
                st.markdown(f"- {reason}")

            left, right = st.columns(2)
            with left:
                st.markdown("**✅ You match**")
                for item in report.matched:
                    st.markdown(f"- {item}")
            with right:
                st.markdown("**⚠️ Gaps**")
                for item in report.gaps:
                    st.markdown(f"- {item}")

            st.markdown("**CV bullets to lead with**")
            st.code("\n".join(f"• {b}" for b in report.cv_bullets), language=None)

            st.markdown("**Note to hiring manager**")
            st.code(report.note_hiring_manager, language=None)
            st.markdown("**Note to recruiter**")
            st.code(report.note_recruiter, language=None)
            st.caption("Tip: the copy icon on each grey box copies the text. Added to your tracker.")

# ---- Tab: Tracker ---------------------------------------------------------
with tab_tracker:
    st.write("Every analysis lands here. Change the **status** as you go. "
             "This list clears when you close the tab, so **download it** to keep it.")
    tracker = pd.DataFrame(st.session_state.tracker, columns=TRACKER_COLUMNS)
    # The editor remembers your status changes itself; we only read what it shows.
    edited = st.data_editor(tracker, width="stretch", num_rows="dynamic", key="tracker_editor")
    st.download_button("Download as CSV (opens in Google Sheets)",
                       edited.to_csv(index=False), file_name="job-tracker.csv")
