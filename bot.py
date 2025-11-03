import os, sqlite3, time, re, json, sys, traceback
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

# -------- Config & Secrets --------
SERPAPI_KEY = os.environ.get("SERPAPI_KEY")
EMAIL_TO = os.environ.get("EMAIL_TO")
EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_APP_PW = os.environ.get("EMAIL_APP_PW")
USE_SMTP = EMAIL_APP_PW is not None

DB_PATH = "data/jobs.db"

# -------- Google query (must be ONE Python string) --------
QUERY = (
    '(site:jobs.lever.co OR site:boards.greenhouse.io OR site:workable.com OR site:careers.icims.com OR site:wd1.myworkdayjobs.com) '
    '("senior project manager" OR "program manager" OR "technical project manager") '
    '("remote - us" OR "remote usa" OR "us-based" OR "us only" OR "united states" OR "us remote" OR "remote in the united states" OR "eligible to work in the us" OR "authorized to work in the us") '
    '("software" OR "technology" OR "digital agency" OR "creative agency" OR "SaaS" OR "product") '
    '-"emea" -"europe" -"uk" -"united kingdom" -"canada" -"australia" -"apac" -"latam" -"mexico" -"global" -"worldwide" '
    '-"hybrid" -"on-site" -"onsite" -"partly remote" -"2 days onsite" -"3 days onsite"'
)

# -------- US-only + fully-remote helper --------
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
    import re as _re
    t = " ".join(text.split()).lower()
    if any(_re.search(p, t) for p in NEG_NON_US): return False
    if any(_re.search(p, t) for p in NEG_NOT_FULLY_REMOTE): return False
    return any(_re.search(p, t) for p in POS_US_REMOTE)

def _jsonld_country_is_us(soup: BeautifulSoup) -> bool:
    try:
        for tag in soup.find_all("script", {"type": ["application/ld+json", "application/json"]}):
            raw = (tag.string or tag.text or "").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
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
    except Exception:
        return False
    return False

def strict_us_remote(url: str, timeout: int = 15) -> bool:
    try:
        r = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Safari/537.36"},
        )
        if r.status_code >= 400:
            return False
        html = r.text
    except Exception:
        return False

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    if _jsonld_country_is_us(soup) and _text_ok(text):
        return True

    url_l = url.lower()
    if any(k in url_l for k in ["remote-us", "remote_usa", "united-states", "us-remote"]) and _text_ok(text):
        return True

    return _text_ok(text)

# -------- Core pipeline --------
def ensure_db():
    # Make sure the folder exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    # (Optional) one-time migration if an old root-level DB exists
    old_path = "jobs.db"
    if os.path.exists(old_path) and not os.path.exists(DB_PATH):
        try:
            os.replace(old_path, DB_PATH)  # move old DB into data/
            print("[INFO] Migrated existing jobs.db to data/jobs.db")
        except Exception as e:
            print(f"[WARN] Could not migrate old DB: {e}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Light safety pragmas (fine for a tiny single-writer workflow)
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")

    # Schema (idempotent)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS seen (
        url TEXT PRIMARY KEY,
        first_seen_utc TEXT,
        title TEXT,
        source TEXT
    )
    """)

    # Helpful index if you later add more tables/joins
    cur.execute("CREATE INDEX IF NOT EXISTS ix_seen_first_seen ON seen(first_seen_utc);")

    conn.commit()
    conn.close()

def google_search_serpapi(q, start=0):
    params = {
        "engine": "google",
        "q": q,
        "num": 100,
        "start": start,
        "tbs": "qdr:d",
        "hl": "en",
        "api_key": SERPAPI_KEY or "",  # avoid KeyError
    }
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
    if r.status_code == 401:
        print("[ERROR] SerpAPI 401 Unauthorized. Check SERPAPI_KEY.")
        return {"organic_results": []}
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
    if not (EMAIL_FROM and EMAIL_TO and USE_SMTP):
        print("[INFO] Email credentials missing; printing digest to logs instead.")
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

def run():
    ensure_db()

    # Always initialize these so NameError cannot happen
    all_items = []
    items = []

    # Fetch up to 200 results from the last day (two pages)
    for start in (0, 100):
        data = google_search_serpapi(QUERY, start=start)
        batch = extract_results(data)
        print(f"[INFO] SerpAPI batch {start}: {len(batch)} results")
        all_items.extend(batch)
        time.sleep(2)

    # De-dupe by URL in this run
    if all_items:
        dedup = {it["url"]: it for it in all_items}
        items = list(dedup.values())
        print(f"[INFO] After dedupe: {len(items)}")
    else:
        print("[WARN] No search results returned from SerpAPI. Check your key or query.")
        items = []

    # New vs seen
    new_items = filter_new(items)
    print(f"[INFO] New (unseen) items: {len(new_items)}")

    # Title sanity check
    new_items = [i for i in new_items if any(k in i["title"].lower() for k in ["project manager", "program manager"])]
    print(f"[INFO] After title filter: {len(new_items)}")

    # Strict US-remote filter (cap to keep runtime reasonable)
    us_remote_items = []
    checked = 0
    for it in new_items[:100]:
        checked += 1
        if strict_us_remote(it["url"]):
            us_remote_items.append(it)
    print(f"[INFO] US-remote kept: {len(us_remote_items)} / {checked} checked")

    # Persist only what we’ll report
    save_seen(us_remote_items)

    subject = f"{len(us_remote_items)} new US-remote PM roles — {datetime.now().date().isoformat()}"
    body = format_markdown(us_remote_items)
    send_email(subject, body)

if __name__ == "__main__":
    try:
        run()
    except Exception:
        print("[FATAL] Unhandled exception:")
        traceback.print_exc()
        # While iterating, avoid failing the workflow hard:
        sys.exit(0)