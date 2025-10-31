# Daily Job Scout

**Daily Job Scout** is an automated Python bot that searches multiple Applicant Tracking Systems (ATS) every hour for new **U.S.-based remote project management jobs**.  
It filters out non-U.S., hybrid, and irrelevant listings, then emails you a daily digest **only when new jobs are found**.  
The bot runs as a **GitHub Actions workflow**, so no servers or manual execution are required.

---

## Features

- Searches **Lever**, **Greenhouse**, **Workable**, **iCIMS**, and **Workday** postings.  
- Filters for **U.S.-based fully remote** roles using content and schema detection.  
- Checks for titles including *Senior Project Manager*, *Program Manager*, or *Technical Project Manager*.  
- Avoids duplicate alerts using a local `jobs.db` SQLite store.  
- Sends an email digest only when new roles are found.  
- Executes **hourly via GitHub Actions** with concurrency control to prevent overlap.  
- Persists state between runs by committing the database back to the repository.

---

## Repository Structure
Daily-Job-Agent/
├── bot.py                 # Core Python script
├── requirements.txt       # Python dependencies
├── .github/
│   └── workflows/
│       └── daily.yml      # GitHub Actions workflow configuration
└── data/
└── jobs.db            # SQLite database of seen jobs (auto-generated)


## Setup

### 1. Fork or clone the repository
```bash
git clone https://github.com/<your-username>/Daily-Job-Agent.git
cd Daily-Job-Agent

2. Configure secrets

Add these in GitHub → Settings → Secrets and variables → Actions:

| Name | Required | Description | SERPAPI_KEY |
|---|---|---|---|
|SERPARI_KEY|✅|API key from SerpAPI￼. Used for Google searches.|
|EMAIL_FROM|✅|Gmail address the bot will send from.| 
|EMAIL_TO|✅|Address to receive job digests.|
|EMAIL_APP_PW|✅|Gmail App Password￼ for authentication.|

Local testing (optional)

Install dependencies:
bash
python -m venv venv
source venv/bin/activate      # or venv\Scripts\activate on Windows
pip install -r requirements.txt

Run manually
bash
python bot.py

You can add a .env file locally:
SERPAPI_KEY=your_key
EMAIL_FROM=you@gmail.com
EMAIL_TO=you@gmail.com #can be an valid email address
EMAIL_APP_PW=your_app_password

Then load it at runtime:
python
from dotenv import load_dotenv
load_dotenv()

Workflow Details

Schedule
The bot runs every hour:
yaml
on:
  schedule:
    - cron: "0 * * * *"   # Every hour at minute 0 (UTC)

Behavior
	•	Fetches up to 100 results from the past 24 hours.
	•	Deduplicates by URL.
	•	Filters by title and content for U.S.-remote roles.
	•	Saves only new results to jobs.db.
	•	Sends an email digest only if new jobs exist.

Concurrency & Safety

yaml
concurrency:
  group: daily-job-scout
  cancel-in-progress: false

#Prevents multiple runs from overlapping.
#The workflow also pushes the updated jobs.db back to your repo for state persistence.

Example emaill digest

Subject: 3 new US-remote PM roles — 2025-10-30

## New remote US PM roles (tech/agency focus)

- [Senior Technical Project Manager – XYZ Agency](https://jobs.lever.co/xyz/123)
  _jobs.lever.co_

- [Program Manager – SaaS Platform](https://boards.greenhouse.io/abc/456)
  _boards.greenhouse.io_

Maintenance Notes
	•	Rate limits: SerpAPI’s free plan allows limited daily queries. Hourly runs will require a paid tier.
	•	Branch protection: If your main branch disallows bot pushes, the workflow will upload the DB as an artifact instead.
	•	Python version: Tested with Python 3.11.
	•	Runtime: ~2–3 minutes per hourly run.

 Customization

Change timing

Edit the cron expression in .github/workflows/daily.yml:

- cron: "*/30 * * * *"   # every 30 minutes

Add job titles or industries

Edit the QUERY constant in bot.py:

("senior project manager" OR "program manager" OR "technical project manager" OR "delivery manager")
("software" OR "digital agency" OR "product" OR "saas")

Adjust filtering strictness

Tweak the regex lists near the top of bot.py:
	•	POS_US_REMOTE — phrases that must appear for U.S. remote jobs.
	•	NEG_NON_US — keywords to exclude.
	•	NEG_NOT_FULLY_REMOTE — hybrid/on-site patterns.

Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Workflow missing “Run workflow” button | File not in default branch, or workflow_dispatch missing | |Ensure .github/workflows/daily.yml is in main and contains workflow_dispatch: {} |
| “SerpAPI 401 Unauthorized” | Invalid or missing SERPAPI_KEY | Check your secret |
| “NameError: items not defined” | Old copy of bot.py or out-of-order code | Use the latest bot.py structure from this repo |
| No email sent | No new jobs found, or email secrets missing | Check logs and secrets
| Non-US or hybrid jobs slipping through | Add stricter patterns in NEG_NON_US and NEG_NOT_FULLY_REMOTE | Tune regex lists |
