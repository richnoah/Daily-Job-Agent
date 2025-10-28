import os, sqlite3, time, re, json
from datetime import datetime, timedelta, timezone
import requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup

SERPAPI_KEY = os.environ["SERPAPI_KEY"]
EMAIL_TO = os.environ["EMAIL_TO"]          # where to send the digest
EMAIL_FROM = os.environ["EMAIL_FROM"]      # Gmail address to send from
EMAIL_APP_PW = os.environ.get("EMAIL_APP_PW")  # Gmail App Password (recommended)
USE_SMTP = EMAIL_APP_PW is not None

DB_PATH = "jobs.db"

# Query: US-only, fully-remote leaning, tech/agency focus, ATS-scoped
QUERY = (
    '(site:jobs.lever.co OR site:boards.greenhouse.io OR site:workable.com OR site:careers.icims.com OR site:wd1.myworkdayjobs.com) '
    '("senior project manager" OR "program manager" OR "technical project manager") '
    '("remote - us" OR "remote usa" OR "us-based" OR "us only" OR "united states" OR "us remote" OR "remote in the united states" OR "eligible to work in the us" OR "authorized to work in the us") '
    '("software" OR "technology" OR "digital agency" OR "creative agency" OR "SaaS" OR "product") '
    '-"emea" -"europe" -"uk" -"united kingdom" -"canada" -"australia" -"apac" -"latam" -"mexico" -"global" -"worldwide" '
    '-"hybrid" -"on-site" -"onsite" -"partly remote" -"2 days onsite" -"3 days onsite"'
)

# -----------------------------
# US-only + Fully-remote filter
# -----------------------------
POS_US_REMOTE = [
    r"\bremote\s*-\s*us\b",
    r"\bus[-\s]?based\b",
    r"\bus\s+only\b",
    r"\bunited states\b",
    r"\bus\s+remote\b",
    r"\beligible to work in the us\b",
    r"\bauthorized to work in the us\b",
]
NEG_NON_US = [
    r"\bemea\b", r"\beurope\b", r"\buk\b", r"\bunited kingdom\b",
    r"\bcanada\b", r"\baustralia\b", r"\bapac\b", r"\blatam\b", r"\bmexico\b",
    r"\bglobal\b", r"\bworldwide\b"
]
NEG_NOT_FULLY_REMOTE = [
    r"\bhybrid\b", r"\bon[\-\s]?site\b", r"\b2 days on[-\s]?site\b", r"\b3 days on[-\s]?site\b",
    r"\bpartly remote\b", r"\bcommut(e|ing)\b"
]

def _text_ok(text: str) -> bool:
    t = " ".join(text.split()).lower()
    if any(re.search(p, t) for p in NEG_NON_US): return False
    if any(re.search(p, t) for p in NEG_NOT_FULLY_REMOTE): return False
    return any(re.search(p, t) for p in POS_US_REMOTE)

def _jsonld_country_is_us(soup: BeautifulSoup) -> bool:
    # Many ATS pages embed schema.org JobPosting; check addressCountry
    for tag in soup.find_all("script", {"type": ["application/ld+json", "application/json"]}):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        objs = data if isinstance(data, list) else [data]
        for obj in objs:
            if not isinstance(obj, dict): 
                continue
            if obj.get("@type") not in ("JobPosting",):
                continue
            locs = obj.get("jobLocation", [])
            if not isinstance(locs, list):
                locs = [locs]
            for loc in locs:
                addr = (loc or {}).get("address", {}) or {}
                country = (addr.get("addressCountry") or "").strip().lower()
                if country in ("us", "usa", "united states"):
                    return True
    return False

def strict_us_remote(url: str, timeout: int = 15) -> bool:
    """Return True only if the job page reads as US-only AND fully remote."""
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code >= 400:
            return False
        html = r.text
    except Exception:
        return False

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # JSON-LD + text rules
    if _jsonld_country_is_us(soup) and _text_ok(text):
        return True

    # URL hints + text rules
    url_l = url.lower()
    if any(k in url_l for k in ["remote-us", "remote_usa", "united-states", "us-remote"]) and _text_ok(text):
        return True

    # Fallback: text-only rules
    return _text_ok(text)

# -----------------------------

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

filtered = [] #filter to remove most non-us and hybrid roles
for it in items:
    if strict_us_remote(it["url"]):
        filtered.append(it)

# optional: log why others were dropped (count only)
print(f"Filtered to US-only fully-remote: {len(filtered)} / {len(items)}")

def is_job_post(url):
    # Light heuristic to reduce non-job pages
    return any(
        part in url.lower()
        for part in ["jobs.lever.co", "boards.greenhouse.io", "workable.com", "careers.icims.com", "myworkdayjobs.com", "recruitee.com"]
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

    # New vs seen
    new_items = filter_new(items)

    # Optional: quick content sniff to exclude “Careers landing pages”
    new_items = [i for i in new_items if any(k in i["title"].lower() for k in ["project manager", "program manager"])]

    # *** Strict US-remote filter (fetches each page; keep cap small if needed) ***
    us_remote_items = []
    for it in new_items[:50]:  # cap to reduce runtime; adjust as needed
        if strict_us_remote(it["url"]):
            us_remote_items.append(it)

    # Persist only the items we’ll actually report
    save_seen(us_remote_items)

    subject = f"{len(us_remote_items)} new US-remote PM roles — {datetime.now().date().isoformat()}"
    body = format_markdown(us_remote_items)
    send_email(subject, body)

if __name__ == "__main__":
    main()