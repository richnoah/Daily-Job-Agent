import os, sqlite3, time
from datetime import datetime, timedelta, timezone
import requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup

SERPAPI_KEY = os.environ["SERPAPI_KEY"]
EMAIL_TO = os.environ["EMAIL_TO"]          # where to send the digest
EMAIL_FROM = os.environ["EMAIL_FROM"]      # Gmail address you’ll send from
EMAIL_APP_PW = os.environ.get("EMAIL_APP_PW")  # Gmail App Password (recommended)
USE_SMTP = EMAIL_APP_PW is not None

DB_PATH = "jobs.db"

QUERY = (
    '(site:jobs.lever.co OR site:boards.greenhouse.io OR site:workable.com '
    'OR site:careers.icims.com OR site:wd1.myworkdayjobs.com) '
    '("senior project manager" OR "program manager" OR "technical project manager") '
    '("remote" OR "Remote - US" OR "Remote USA" OR "US-based" OR "United States" OR "U.S.") '
    '("software" OR "technology" OR "digital agency" OR "creative agency" OR "marketing technology" OR "product development" OR "SaaS") '
    '-site:indeed.com -site:linkedin.com -site:glassdoor.com'
)

def ensure_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS seen (
        url TEXT PRIMARY KEY,
        first_seen_utc TEXT,
        title TEXT,
        source TEXT
    )
    """)
    conn.commit()
    conn.close()

def google_search_serpapi(q, start=0):
    # tbs=qdr:d -> results from past day; hl=en; num up to 100
    params = {
        "engine": "google",
        "q": q,
        "num": 100,
        "start": start,
        "tbs": "qdr:d",
        "hl": "en",
        "api_key": SERPAPI_KEY
    }
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def extract_results(payload):
    items = []
    for r in payload.get("organic_results", []):
        link = r.get("link")
        title = r.get("title")
        source = r.get("source")
        if link and title:
            items.append({"url": link, "title": title, "source": source or ""})
    return items

def is_job_post(url):
    # Light heuristic to reduce non-job pages
    return any(
        part in url.lower()
        for part in ["jobs.lever.co", "boards.greenhouse.io", "workable.com", "careers.icims.com", "myworkdayjobs.com"]
    )

def filter_new(items):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    new = []
    for it in items:
        if not is_job_post(it["url"]):
            continue
        cur.execute("SELECT 1 FROM seen WHERE url = ?", (it["url"],))
        if cur.fetchone() is None:
            new.append(it)
    conn.close()
    return new

def save_seen(items):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    for it in items:
        try:
            cur.execute(
                "INSERT OR IGNORE INTO seen (url, first_seen_utc, title, source) VALUES (?,?,?,?)",
                (it["url"], now, it["title"], it.get("source","")),
            )
        except sqlite3.Error:
            pass
    conn.commit()
    conn.close()

def format_markdown(items):
    if not items:
        return "No new matches today."
    lines = ["## New remote US PM roles (tech/agency focus)", ""]
    for it in items:
        lines.append(f"- [{it['title']}]({it['url']})  \n  _{it.get('source','')}_")
    return "\n".join(lines)

def send_email(subject, body_md):
    if not USE_SMTP:
        print("EMAIL_APP_PW not set; printing output instead:\n")
        print(subject)
        print(body_md)
        return
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(body_md, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(EMAIL_FROM, EMAIL_APP_PW)
        s.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())

def main():
    ensure_db()

    # Fetch up to 200 results from the last day (two pages)
    all_items = []
    for start in (0, 100):
        data = google_search_serpapi(QUERY, start=start)
        all_items.extend(extract_results(data))
        time.sleep(2)

    # De-dupe by URL in this run
    dedup = {}
    for it in all_items:
        dedup[it["url"]] = it
    items = list(dedup.values())

    new_items = filter_new(items)
    # Optional: quick content sniff to exclude “Careers landing pages”
    new_items = [i for i in new_items if any(k in i["title"].lower() for k in ["project manager", "program manager"])]

    # Persist new URLs so we don’t re-send tomorrow
    save_seen(new_items)

    subject = f"{len(new_items)} new US-remote PM roles — {datetime.now().date().isoformat()}"
    body = format_markdown(new_items)
    send_email(subject, body)

if __name__ == "__main__":
    main()