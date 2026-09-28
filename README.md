# Job Copilot — setup guide (no coding experience needed)

You paste a job description; the app scores your fit, lists matches and gaps, picks CV bullets from
your own CV, drafts referral notes, and logs everything to a tracker. Cost: ₹0.

Total setup time: about 20 minutes, once.

---

## Step 1 — Get a free Gemini API key (3 min)

1. Go to **aistudio.google.com/apikey** and sign in with your Google account.
2. Click **Create API key**. Copy it somewhere safe.
3. Treat it like a password: don't share it, don't post it on GitHub.

No card is needed for the free tier. Free-tier limits change; you can see yours in AI Studio.

## Step 2 — Install Python (5 min)

**Mac:** open **Terminal** (Cmd+Space → type "Terminal"). Type `python3 --version` and press Enter.
If you see `Python 3.10` or higher, skip ahead. If not, download it from **python.org/downloads**
and run the installer.

**Windows:** download from **python.org/downloads**. In the installer, **tick "Add python.exe to
PATH"** before clicking Install. Then open **Command Prompt** (Start → type "cmd").

## Step 3 — Put the project folder somewhere easy

Unzip `job-copilot.zip` into your Documents folder, so you have `Documents/job-copilot/app.py`.

In Terminal / Command Prompt, move into that folder:

```
cd Documents/job-copilot
```

## Step 4 — Install the libraries (2 min)

Mac:
```
python3 -m pip install -r requirements.txt
```
Windows:
```
python -m pip install -r requirements.txt
```

## Step 5 — Run it

Mac: `python3 -m streamlit run app.py`
Windows: `python -m streamlit run app.py`

Your browser opens at `http://localhost:8501`. That's your app. To stop it, go back to the
terminal and press **Ctrl+C**.

## Step 6 — First use

1. Paste your API key in the sidebar.
2. **My CV** tab → upload your CV (or paste it) and write what you're looking for.
3. **Analyse a job** tab → paste a full JD → **Analyse**.

Your CV and tracker are private to your browser tab and clear when you close it. Keep your CV
file handy to re-upload, and use **Download as CSV** on the Tracker tab to keep your list.

Optional, so you don't paste the key every time: create a folder `.streamlit` inside
`job-copilot`, and inside it a file `secrets.toml` containing one line:
```
GEMINI_API_KEY = "paste-your-key-here"
```

---

## Job alerts — see fresh jobs that match your CV

The **Job alerts** tab searches two free job sources, removes duplicates, scores every job
against your CV, and lists the best matches first, each with an **Open job** link.

**Get the free keys once (about 10 minutes):**

*JSearch* — jobs from LinkedIn, Indeed, Glassdoor and more; 200 free searches a month
1. Go to **rapidapi.com** and sign up.
2. Search for **JSearch**, open it, click **Pricing**, subscribe to the free **Basic** plan.
3. Open the **Endpoints** tab and copy the value shown as **X-RapidAPI-Key**.

*Adzuna* — free; covers India, UK, USA, Australia, Canada (not UAE)
1. Go to **developer.adzuna.com** and click **Register**.
2. Open **Dashboard → API Access Details** and copy the **Application ID** and **Application Key**.

Paste them in the sidebar under **Job alert sources**. One source is enough; both gives more jobs.

**Use it:** set up to 5 searches (job title + city + country), pick how recent, click
**Find jobs**. Each search row uses 1 of your 200 free JSearch searches, so 5 rows once a day
is about 150 a month. On each job you can **Open job**, **Analyse in detail** (sends it to the
Analyse tab) or **Add to tracker**.

To save these keys on your laptop too, add these lines to `.streamlit/secrets.toml`:
```
JSEARCH_KEY = "your-jsearch-key"
ADZUNA_APP_ID = "your-adzuna-id"
ADZUNA_APP_KEY = "your-adzuna-key"
```
Never add them on the Streamlit website — visitors would use your quota.

---

## If something goes wrong

| You see | What it means | Fix |
|---|---|---|
| `command not found: python3` / `'python' is not recognized` | Python isn't installed or not on PATH | Re-do Step 2 (Windows: tick "Add to PATH") |
| `No module named streamlit` | Step 4 didn't run in this folder | `cd` into `job-copilot`, redo Step 4 |
| `429` or `RESOURCE_EXHAUSTED` | Free-tier limit hit | Wait a minute, or pick another model in the sidebar |
| `404` / `model not found` | That model isn't on your key | Pick another model in the sidebar |
| `API key not valid` | Key copied wrong | Paste it again, no spaces |
| `JSearch refused your key` | Key wrong, or not subscribed to the free Basic plan | Re-copy the key; check Pricing → Basic is subscribed |
| `No module named requests` | New library not installed yet | Redo Step 4 |

When stuck, copy the red error text and paste it to Claude.

---

## How the code works (read `app.py` alongside this)

The file has 9 numbered sections:

1. **Imports** — borrowing libraries: Streamlit (web page), Gemini (AI), pandas (tables),
   requests (fetching jobs), pypdf and python-docx (reading CV files).
2. **Settings** — models, countries, how many jobs to fetch. Safe to change.
3. **The answer's shapes** — the most important AI idea here: *structured output*.
   We give Gemini a form with fixed fields (score, verdict, gaps...) and it must fill it in.
   `FitReport` is the form for one job in detail; `JobMatches` is a short form for many jobs.
4. **The prompts** — the instructions. This is where your product judgement lives: honesty rules,
   "never invent a metric", how strict the alerts should be. Edit them and watch the output change.
5. **Talking to Gemini** — one helper that sends a prompt and gets the form back. Alerts score
   all jobs in ONE call, which saves your free quota.
6. **Fetching jobs** — asks JSearch and Adzuna for jobs, turns both into the same simple format,
   and removes duplicates.
7. **Reading an uploaded CV** — turns a PDF, Word or text file into plain text.
8. **Each visitor's private memory** — `st.session_state` is a notebook Streamlit keeps for each
   browser tab. That's what keeps one visitor's CV away from another's.
9. **The web page** — four tabs. Streamlit reruns the whole file top-to-bottom every time you
   click something; that's normal.

## Learn by changing it — do these in order

1. **Change a rule.** In section 4, add: *"Treat any role below Director level as Skip."*
   Save the file, refresh the browser, analyse the same JD. Did the verdict change?
2. **Add a field.** In section 3 add
   `interview_questions: list[str] = Field(description="5 likely interview questions for this role")`.
   Then in section 9, after the recruiter note, show it:
   ```python
   st.markdown("**Likely interview questions**")
   for q in report.interview_questions:
       st.markdown(f"- {q}")
   ```
3. **Build your first eval** (the interview-worthy part). Pick 10 JDs you've already judged
   yourself as Apply / Maybe / Skip. Run each through the app. Count how often the app's verdict
   matches yours. That match rate is your baseline; every prompt change should improve it, not
   just "feel better". Same discipline as an offline eval framework, at personal scale.

## Share it as a link (free)

Each visitor uploads their own CV and pastes their own free Gemini key. Your key is never used.

1. **Make a free GitHub account** at github.com. GitHub is where the code lives online.
2. Click **+** (top right) → **New repository**. Name it `job-copilot`, choose **Public**,
   click **Create repository**.
3. Click **uploading an existing file**. Drag in only these three files: `app.py`,
   `requirements.txt`, `README.md`. Click **Commit changes**.
   **Never upload the `.streamlit` folder** — it holds your key.
4. Go to **share.streamlit.io**, sign in with your GitHub account, click **Create app**, pick
   the `job-copilot` repository, main file `app.py`, then **Deploy**.
5. In a few minutes you get a link like `job-copilot.streamlit.app`. Share that.

Don't add your key in the Streamlit website's "Secrets" settings. If you did, every visitor
would use your key.

To update the online app later, upload the changed `app.py` to GitHub again. The website
picks it up by itself.

## What comes next (v3 ideas)

- Write to your Google Sheet tracker directly instead of CSV.
- Paste a URL instead of the JD text.
- Save your eval set as a file and run all 10 with one button.
