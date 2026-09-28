"""
JOB COPILOT  —  v3 (job alerts)
-------------------------------
Paste a job description. The AI tells you:
  * how well it fits YOU (score 0-100 and a verdict: Apply / Maybe / Skip)
  * what you match and what you're missing
  * which CV bullets to use (built only from your own CV, no invented numbers)
  * a referral note for the hiring manager and one for the recruiter

New in v3 — JOB ALERTS:
  * Save a few searches (job title + city + country).
  * The app pulls fresh jobs from two free job sources (JSearch and Adzuna),
    removes duplicates, and asks Gemini to score every job against your CV.
  * You get one list, best matches first, each with a link to the job.

Still shareable: each visitor uses their own CV and their own free keys.
Everything a visitor enters stays private to their own browser tab.

How to run it:   streamlit run app.py
(See README.md for the full step-by-step setup.)

Read this file top to bottom. Each numbered section does ONE job.
"""

# ─────────────────────────────────────────────────────────────
# 1. IMPORTS — borrowing tools other people already wrote
# ─────────────────────────────────────────────────────────────
import io                        # lets us treat an uploaded file like a file on disk
import time                      # lets us wait a few seconds before retrying
from datetime import date        # stamp today's date on each tracker row

import pandas as pd              # tables: used for the tracker and searches
import requests                  # fetches data from websites (the job sources)
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

# Countries you can search, with the short codes the job sources use.
# Adzuna doesn't cover every country (e.g. UAE) — for those only JSearch is used.
COUNTRIES = {"India": "in", "UAE": "ae", "UK": "gb", "USA": "us", "Singapore": "sg",
             "Australia": "au", "Canada": "ca"}
ADZUNA_COUNTRIES = {"in", "gb", "us", "au", "ca"}

# How recent the jobs should be. JSearch uses words, Adzuna uses a number of days.
POSTED_WITHIN = {"Last 24 hours": ("today", 1), "Last 3 days": ("3days", 3),
                 "Last week": ("week", 7), "Last month": ("month", 30)}

MAX_SEARCHES = 5          # each search row uses 1 JSearch request (200 free a month)
RESULTS_PER_SOURCE = 10   # jobs fetched per search, per source


# ─────────────────────────────────────────────────────────────
# 3. THE ANSWER'S SHAPE — "structured output"
#    Instead of free text, we ask the AI to fill in a form.
#    That's what makes the result reliable enough to put in a table.
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


# The shape for scoring MANY jobs at once in the alerts tab (short answers only)
class JobMatch(BaseModel):
    id: str = Field(description="The job's id, copied exactly")
    score: int = Field(description="0-100 fit against the CV and criteria")
    verdict: str = Field(description="Exactly one of: Apply, Maybe, Skip")
    reason: str = Field(description="One short sentence explaining the score")


class JobMatches(BaseModel):
    matches: list[JobMatch]


# ─────────────────────────────────────────────────────────────
# 4. THE INSTRUCTIONS WE GIVE THE AI (the "prompts")
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

ALERTS_INSTRUCTIONS = """
You screen job listings for an experienced professional.
For EVERY job in the list, score how well it fits the candidate's CV AND criteria (0-100),
give a verdict (Apply, Maybe or Skip) and one short, specific reason.

Rules:
- Be honest and strict. Most listings should NOT be Apply.
- Criteria are hard filters: wrong level, wrong city or a stated deal-breaker means Skip.
- Listings are often short; judge on what's there and say so if it's too thin to tell.
- Return one entry per job, using each job's id exactly as given.
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
BUSY_SIGNS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "overloaded", "high demand")
MISSING_SIGNS = ("404", "NOT_FOUND")


def is_busy(err) -> bool:
    return any(s in str(err) for s in BUSY_SIGNS)


def ask_gemini(api_key: str, model: str, instructions: str, prompt: str, shape):
    """Send one prompt and get back an answer filled into the given shape.
    If Google says it's busy, we wait a little and try again, then try the other models."""
    client = genai.Client(api_key=api_key)
    models_to_try = [model] + [m for m in MODELS if m != model]
    last_err = None
    for m in models_to_try:
        for wait in (0, 3, 8):          # seconds to wait before each attempt
            time.sleep(wait)
            try:
                response = client.models.generate_content(
                    model=m,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=instructions,
                        response_mime_type="application/json",  # "answer in JSON..."
                        response_schema=shape,                  # "...shaped exactly like this form"
                        temperature=0.3,                        # low = more consistent answers
                    ),
                )
                # The SDK usually fills in the shape for us. If not, we do it ourselves.
                if isinstance(response.parsed, shape):
                    return response.parsed
                return shape.model_validate_json(response.text)
            except Exception as err:
                last_err = err
                if any(s in str(err) for s in MISSING_SIGNS):
                    break               # this model isn't on your key — move to the next model
                if not is_busy(err):
                    raise               # a real problem (e.g. wrong key) — no point retrying
    raise last_err


def analyse_job(api_key: str, model: str, cv: str, criteria: str, jd: str) -> FitReport:
    return ask_gemini(api_key, model, SYSTEM_INSTRUCTIONS, build_prompt(cv, criteria, jd), FitReport)


def score_jobs(api_key: str, model: str, cv: str, criteria: str, jobs: list[dict]) -> dict:
    """Score all fetched jobs in ONE call (saves your free quota). Returns {job id: match}."""
    listing = "\n\n".join(
        f"[id: {j['id']}] {j['title']} — {j['company']} — {j['location']}\n{j['description'][:700]}"
        for j in jobs
    )
    prompt = f"""=== CANDIDATE CV ===
{cv[:20000]}

=== CANDIDATE'S JOB CRITERIA ===
{criteria or "None given."}

=== JOB LISTINGS ({len(jobs)}) ===
{listing}"""
    result = ask_gemini(api_key, model, ALERTS_INSTRUCTIONS, prompt, JobMatches)
    return {m.id: m for m in result.matches}


# ─────────────────────────────────────────────────────────────
# 6. FETCHING JOBS FROM THE TWO FREE SOURCES
#    Both return lists of jobs; we turn each into the same simple dictionary
#    so the rest of the app doesn't care where a job came from.
# ─────────────────────────────────────────────────────────────
class SourceError(Exception):
    """A friendly error message about a job source."""


JSEARCH_HOST = "jsearch.p.rapidapi.com"
# JSearch renamed its search address in 2026. We try the new one first, then the old one.
JSEARCH_PATHS = ["/search-v2", "/search"]


def fetch_jsearch(key: str, title: str, city: str, country: str, posted: str) -> list[dict]:
    """JSearch: LinkedIn, Indeed, Glassdoor and more, via Google for Jobs."""
    headers = {"X-RapidAPI-Key": key.strip(), "X-RapidAPI-Host": JSEARCH_HOST}
    base = {"query": f"{title} in {city}", "country": country, "date_posted": posted}
    resp = None
    for path in JSEARCH_PATHS:
        params = dict(base) if path.endswith("v2") else {**base, "page": 1, "num_pages": 1}
        resp = requests.get(f"https://{JSEARCH_HOST}{path}", headers=headers, params=params, timeout=45)
        if resp.status_code == 400:
            # Some setups reject the country filter; try once more without it
            params.pop("country")
            resp = requests.get(f"https://{JSEARCH_HOST}{path}", headers=headers, params=params, timeout=45)
        if resp.status_code != 404:
            break   # this address exists — use its answer
    if resp.status_code in (401, 403):
        raise SourceError("JSearch refused your key. Check it, and that you've subscribed to "
                          "JSearch's free Basic plan on RapidAPI.")
    if resp.status_code == 429:
        raise SourceError("JSearch's free monthly limit is used up. It resets next month.")
    if not resp.ok:
        raise SourceError(f"JSearch error {resp.status_code}: {resp.text[:200]}")
    data = resp.json().get("data") or []
    if isinstance(data, dict):   # the newer address may wrap the list one level deeper
        data = data.get("jobs") or data.get("data") or []
    jobs = []
    for j in data[:RESULTS_PER_SOURCE]:
        place = ", ".join(p for p in [j.get("job_city"), j.get("job_country")] if p)
        jobs.append({
            "id": "js-" + str(j.get("job_id", "")),
            "title": j.get("job_title") or "Untitled role",
            "company": j.get("employer_name") or "Unknown",
            "location": place or ("Remote" if j.get("job_is_remote") else "Not stated"),
            "posted": (j.get("job_posted_at_datetime_utc") or "")[:10],
            "link": j.get("job_apply_link") or j.get("job_google_link") or "",
            "source": "JSearch · " + (j.get("job_publisher") or "web"),
            "description": j.get("job_description") or "",
        })
    return jobs


def fetch_adzuna(app_id: str, app_key: str, title: str, city: str, country: str, days: int) -> list[dict]:
    """Adzuna: a large job search engine with a free developer API."""
    if country not in ADZUNA_COUNTRIES:
        return []   # Adzuna doesn't cover this country; JSearch still does
    resp = requests.get(
        f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
        params={"app_id": app_id, "app_key": app_key, "what": title, "where": city,
                "max_days_old": days, "results_per_page": RESULTS_PER_SOURCE,
                "content-type": "application/json"},
        timeout=30,
    )
    if resp.status_code in (401, 403):
        raise SourceError("Adzuna refused your App ID / App key. Check both.")
    if resp.status_code == 429:
        raise SourceError("Adzuna's free limit is used up for now. Try again later.")
    if not resp.ok:
        raise SourceError(f"Adzuna error {resp.status_code}: {resp.text[:200]}")
    jobs = []
    for j in resp.json().get("results") or []:
        jobs.append({
            "id": "az-" + str(j.get("id", "")),
            "title": j.get("title") or "Untitled role",
            "company": (j.get("company") or {}).get("display_name") or "Unknown",
            "location": (j.get("location") or {}).get("display_name") or "Not stated",
            "posted": (j.get("created") or "")[:10],
            "link": j.get("redirect_url") or "",
            "source": "Adzuna",
            "description": j.get("description") or "",
        })
    return jobs


def remove_duplicates(jobs: list[dict]) -> list[dict]:
    """The same job often shows up in both sources; keep the first copy."""
    seen, unique = set(), []
    for j in jobs:
        key = (j["title"].lower().strip(), j["company"].lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique


# ─────────────────────────────────────────────────────────────
# 7. READING AN UPLOADED CV (PDF, Word or text)
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
# 8. EACH VISITOR'S PRIVATE MEMORY ("session state")
#    st.session_state is a small notebook Streamlit keeps for EACH browser tab.
#    Visitor A never sees visitor B's notebook. It's wiped when the tab closes,
#    which is why the tracker has a Download button.
# ─────────────────────────────────────────────────────────────
DEFAULT_SEARCHES = pd.DataFrame([
    {"Job title": "Director of Product", "City": "Bangalore", "Country": "India"},
    {"Job title": "Head of Product", "City": "Bangalore", "Country": "India"},
])
for key, start_value in {"cv": "", "criteria": "", "tracker": [], "cv_file_id": None,
                         "jd": "", "jd_link": "", "alerts": [], "searches": DEFAULT_SEARCHES}.items():
    if key not in st.session_state:
        st.session_state[key] = start_value


def add_to_tracker(company, role, location, score, verdict, link, status="Analysed") -> None:
    st.session_state.tracker.append({
        "date": date.today().isoformat(), "company": company, "role": role,
        "location": location, "score": score, "verdict": verdict, "status": status, "link": link,
    })


def from_secrets(name: str) -> str:
    """Only for running on YOUR laptop: reads keys from .streamlit/secrets.toml.
    On the shared online version, never add your keys to the website's secrets —
    then this finds nothing and each visitor pastes their own."""
    try:
        return st.secrets[name]
    except Exception:
        return ""


# Buttons on each alert run these ("callbacks") before the page redraws
def send_to_analyse(job: dict) -> None:
    st.session_state.jd = f"{job['title']} at {job['company']} ({job['location']})\n\n{job['description']}"
    st.session_state.jd_link = job["link"]
    st.session_state.sent_notice = f"Sent “{job['title']}” to the Analyse a job tab."


def track_alert(job: dict) -> None:
    add_to_tracker(job["company"], job["title"], job["location"], job.get("score", ""),
                   job.get("verdict", ""), job["link"], status="Saved from alerts")
    st.session_state.sent_notice = f"Added “{job['title']}” to your tracker."


# ─────────────────────────────────────────────────────────────
# 9. THE WEB PAGE — everything below draws what you see
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="Job Copilot", page_icon="🎯", layout="wide")
st.title("🎯 Job Copilot")
st.caption("Your CV, keys and results stay private to this browser tab. Nothing is stored after you close it.")

with st.sidebar:
    st.header("Your Gemini key")
    api_key = st.text_input("Paste your own Gemini API key", value=from_secrets("GEMINI_API_KEY"),
                            type="password")
    st.markdown("No key yet? It's free and takes 2 minutes: go to "
                "[aistudio.google.com/apikey](https://aistudio.google.com/apikey), "
                "sign in with Google, click **Create API key**, copy it here.")
    model = st.selectbox("Model", MODELS)
    st.caption("Quota error? Pick another model or wait a minute.")

    with st.expander("Job alert sources (optional)"):
        st.caption("Needed only for the Job alerts tab. Add one or both — both are free.")
        jsearch_key = st.text_input("JSearch key (from RapidAPI)", value=from_secrets("JSEARCH_KEY"),
                                    type="password")
        adzuna_id = st.text_input("Adzuna App ID", value=from_secrets("ADZUNA_APP_ID"))
        adzuna_key = st.text_input("Adzuna App key", value=from_secrets("ADZUNA_APP_KEY"), type="password")
        st.markdown("How to get them: see **Job alerts → How to get the free keys**.")

tab_job, tab_alerts, tab_profile, tab_tracker = st.tabs(["Analyse a job", "Job alerts", "My CV", "Tracker"])

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

def score_and_store(jobs: list[dict]) -> None:
    """Score the jobs with Gemini and save them, best first. If Gemini is busy,
    keep the jobs anyway (unscored) so the visitor can score them later."""
    with st.spinner(f"Scoring {len(jobs)} jobs against your CV..."):
        try:
            scores = score_jobs(api_key, model, st.session_state.cv, st.session_state.criteria, jobs)
        except Exception as err:
            if is_busy(err):
                st.warning(f"Google's AI is very busy right now, so the {len(jobs)} jobs below aren't "
                           "scored yet. Wait a minute, then click **Score these jobs**.")
            else:
                st.error(f"Found {len(jobs)} jobs, but scoring failed: {err}")
            scores = {}
    for j in jobs:
        m = scores.get(j["id"])
        if m or j.get("score") is None:          # never overwrite a good score with nothing
            j["score"] = m.score if m else None
            j["verdict"] = m.verdict if m else "Not scored"
            j["reason"] = m.reason if m else ""
    jobs.sort(key=lambda j: j["score"] if j["score"] is not None else -1, reverse=True)
    st.session_state.alerts = jobs


# ---- Tab: Job alerts ------------------------------------------------------
with tab_alerts:
    st.write("Find fresh jobs that match your CV. Set up your searches, then click **Find jobs**. "
             "Every job is scored against your CV, best matches first.")

    st.markdown("**Your searches** (up to 5 rows; add or delete rows in the table)")
    searches = st.data_editor(
        st.session_state.searches,
        num_rows="dynamic",
        width="stretch",
        key="searches_editor",
        column_config={"Country": st.column_config.SelectboxColumn(options=list(COUNTRIES), required=True)},
    )
    posted_label = st.selectbox("Posted within", list(POSTED_WITHIN), index=2)
    st.caption("Each search row uses 1 of your 200 free JSearch searches a month. "
               "Adzuna doesn't cover the UAE, so UAE searches use JSearch only.")

    if st.button("Find jobs", type="primary"):
        rows = [r for r in searches.to_dict("records")
                if str(r.get("Job title") or "").strip() and r.get("Country") in COUNTRIES][:MAX_SEARCHES]
        if not api_key:
            st.error("Paste your Gemini API key in the sidebar first (it's free).")
        elif len(st.session_state.cv.strip()) < 100:
            st.error("Go to the 'My CV' tab and upload or paste your CV first.")
        elif not (jsearch_key or (adzuna_id and adzuna_key)):
            st.error("Add at least one job source key in the sidebar under 'Job alert sources'.")
        elif not rows:
            st.error("Add at least one search with a job title and a country.")
        else:
            posted_word, posted_days = POSTED_WITHIN[posted_label]
            found, problems = [], set()
            with st.spinner("Collecting jobs..."):
                for r in rows:
                    title, city = str(r["Job title"]).strip(), str(r.get("City") or "").strip()
                    code = COUNTRIES[r["Country"]]
                    where = city or r["Country"]
                    if jsearch_key:
                        try:
                            found += fetch_jsearch(jsearch_key, title, where, code, posted_word)
                        except SourceError as err:
                            problems.add(str(err))
                        except Exception as err:
                            problems.add(f"JSearch couldn't be reached ({type(err).__name__}: {str(err)[:150]}).")
                    if adzuna_id and adzuna_key:
                        try:
                            found += fetch_adzuna(adzuna_id, adzuna_key, title, city, code, posted_days)
                        except SourceError as err:
                            problems.add(str(err))
                        except Exception as err:
                            problems.add(f"Adzuna couldn't be reached ({type(err).__name__}: {str(err)[:150]}).")
            for p in problems:
                st.warning(p)
            jobs = remove_duplicates(found)
            if not jobs:
                st.info("No jobs found. Try a wider time window, a broader job title, or another city.")
                st.session_state.alerts = []
            else:
                score_and_store(jobs)

    notice = st.session_state.get("sent_notice")
    if notice:
        st.success(notice)
        del st.session_state["sent_notice"]

    alerts = st.session_state.alerts
    if alerts:
        st.divider()
        unscored = sum(1 for j in alerts if j["score"] is None)
        if unscored:
            st.info(f"{unscored} jobs aren't scored yet. This doesn't use any JSearch searches.")
            if st.button("Score these jobs"):
                score_and_store([dict(j) for j in alerts])
                st.rerun()
        f1, f2 = st.columns(2)
        min_score = f1.slider("Show jobs scoring at least", 0, 100, 50, step=5)
        verdicts = f2.multiselect("Verdict", ["Apply", "Maybe", "Skip", "Not scored"],
                                  default=["Apply", "Maybe", "Not scored"])
        shown = [j for j in alerts if j["verdict"] in verdicts
                 and (j["score"] is None or j["score"] >= min_score)]
        st.markdown(f"**{len(shown)} of {len(alerts)} jobs** match your filters")

        for j in shown:
            with st.container(border=True):
                head, score_col = st.columns([5, 1])
                head.markdown(f"**{j['title']}** — {j['company']}")
                head.caption(" · ".join(x for x in [j["location"], j["source"],
                                                    f"posted {j['posted']}" if j["posted"] else ""] if x))
                badge = {"Apply": "🟢", "Maybe": "🟡", "Skip": "🔴"}.get(j["verdict"], "⚪")
                score_col.markdown(f"### {'–' if j['score'] is None else j['score']}")
                score_col.caption(f"{badge} {j['verdict']}")
                if j["reason"]:
                    st.write(j["reason"])
                b1, b2, b3 = st.columns(3)
                if j["link"]:
                    b1.link_button("Open job ↗", j["link"])
                b2.button("Analyse in detail", key="an-" + j["id"], on_click=send_to_analyse, args=(j,))
                b3.button("Add to tracker", key="tr-" + j["id"], on_click=track_alert, args=(j,))

    with st.expander("How to get the free keys"):
        st.markdown("""
**JSearch** (LinkedIn, Indeed, Glassdoor and more; 200 free searches a month)
1. Go to **rapidapi.com** and sign up (free).
2. Search for **JSearch**, open it, click **Pricing**, and subscribe to the free **Basic** plan.
3. Open the **Endpoints** tab and copy the value shown as **X-RapidAPI-Key**. Paste it in the sidebar.

**Adzuna** (free; covers India, UK, USA, Australia, Canada)
1. Go to **developer.adzuna.com** and click **Register**.
2. After signing in, open **Dashboard → API Access Details**.
3. Copy the **Application ID** and **Application Key** into the sidebar.
""")

# ---- Tab: Analyse a job ---------------------------------------------------
with tab_job:
    link = st.text_input("Job link (optional, just for your tracker)", key="jd_link")
    jd_text = st.text_area("Paste the full job description", key="jd", height=300)

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

            add_to_tracker(report.company, report.role_title, report.location,
                           report.fit_score, report.verdict, link)

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
