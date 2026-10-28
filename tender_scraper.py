"""
Daleel Madani tender scraper — v8 (v8: also runs in the cloud on GitHub Actions; v7: matches ChromeDriver to Chrome)
- Captures Calls for Tenders / Calls for Proposals
- Safe incremental save (never wipes history on a corrupted file)
- Detects blocked / failed pages instead of treating them as "end of list"
- Scope tagging (Works / Supply / Services / Other / Cancelled) + Relevant flag
- Reference number extracted from the title (else To Verify)
- Deadline converted to a real date, Days_Left, Expired / Cancelled status
- CSV export (Arabic-safe for Excel) for the Tender Register
- Organisation, exact deadline and description read from the listing card
- DEBUG_SAVE_HTML: saves page 1 of each category so selectors can be checked

Run:  python tender_scraper.py
"""

import csv
import json
import os
import re
import shutil
import time
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
import undetected_chromedriver as uc
from bs4 import BeautifulSoup

uc.Chrome.__del__ = lambda self: None  # avoid WinError 6 on exit; quit() is called explicitly

# ───────────────────────── CONFIG ─────────────────────────
BASE_URL = "https://daleel-madani.org"
CATEGORIES = {
    "/type-call/calls-tenders": "Calls for Tenders",
    "/type-call/call-proposals": "Calls for Proposals",
}
DATA_FILE = "daleel_madani_tenders.json"
# v4: set by run_tenders.bat so the CSV lands in the Google Drive folder
CSV_FILE = os.environ.get("DALEEL_CSV", "daleel_madani_tenders.csv")

MAX_PAGES = 50              # hard ceiling per category
STOP_AFTER_EMPTY_PAGES = 3  # stop after N consecutive pages with no new items
REQUEST_TIMEOUT = 30        # seconds
PAGE_DELAY = 2              # seconds between pages
BROWSER_WAIT_MAX = 40       # seconds to wait for the listing to load
CHROME_MAJOR = None         # leave None = read installed Chrome version from Windows. Set e.g. 155 only if that fails.
DEBUG_SAVE_HTML = False     # saves debug_calls-tenders.html / debug_call-proposals.html (page 1)

# ── Scope tagging (pre-filter only; Bid/No-Bid is still the manager's call) ──
# Edit these lists freely. Short English words are matched as whole words.
WORKS_KEYWORDS = [
    "construction", "rehabilitation", "renovation", "civil", "works", "wash",
    "water", "sanitation", "sewage", "sewer", "solar", "pv", "electrical", "mep",
    "plumbing", "reverse osmosis", "ro", "shelter",
    "infrastructure", "road", "tank", "borehole", "pump", "generator", "hvac",
    "epoxy", "floor", "flooring", "paint", "painting", "tiling", "roof", "roofing",
    "waterproofing", "concrete", "fencing", "boq", "demolition", "latrine",
    "تأهيل", "ترميم", "أشغال", "مياه", "صرف صحي", "طاقة شمسية", "بناء", "صيانة",
    "كهرباء", "تمديدات",
]
# Supply lines Valeo/PMM has delivered before — marked Relevant even without works
FOCUS_SUPPLY_KEYWORDS = [
    "hygiene", "nfi", "nfis", "non-food", "non food", "food kit", "food kits",
    "food parcel", "food parcels", "dignity kit", "mhm", "cleaning",
    "construction material", "building material", "pipes", "cables",
    "electrical consumables", "plumbing materials",
    "مواد بناء", "مستلزمات النظافة", "حصص غذائية",
]
SUPPLY_WORDS = ["supply", "delivery", "provision", "procurement", "purchase", "توريد"]
SERVICE_WORDS = ["services", "service", "transport", "transportation", "rental",
                 "consultancy", "consultant", "training", "نقل", "خدمات"]
CANCEL_WORDS = ["cancelled", "canceled", "ملغى", "إلغاء"]

DATE_FORMATS = ["%A, %d %B %Y", "%d %B %Y", "%A %d %B %Y", "%d/%m/%Y", "%Y-%m-%d"]

CARD_SELECTORS = [("div", "node-teaser"), ("div", "views-row")]


class BlockedError(Exception):
    """Raised when the site returns a challenge/forbidden page."""


# ───────────────────────── STORAGE ─────────────────────────
def load_existing_tenders(filename=DATA_FILE):
    if not os.path.exists(filename):
        return []
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("JSON root is not a list")
        return data
    except (json.JSONDecodeError, ValueError) as e:
        # Never silently return [] — that would overwrite the full history.
        backup = f"{filename}.corrupt_{datetime.now():%Y%m%d_%H%M%S}"
        shutil.copy2(filename, backup)
        raise SystemExit(
            f"ERROR: {filename} is unreadable ({e}). Backed up to {backup}. "
            "Fix or restore it before running again."
        )


def save_tenders(data, filename=DATA_FILE):
    tmp = filename + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp, filename)  # atomic: old file stays intact if writing fails


def export_csv(data, filename=CSV_FILE):
    fields = ["Scraped_At", "Status", "Relevant", "Scope", "Category", "Organization",
              "Org_Check", "Reference", "Reference_Check", "Title", "Deadline", "Days_Left",
              "Deadline_Check", "Docs_Status", "Docs_Count", "Docs_Folder", "External_Links",
              "Contact_Emails", "Deadline_Raw", "Summary", "Link"]
    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
    tmp = filename + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig = Arabic OK in Excel
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(data)
    _replace_with_retry(tmp, filename)


def _replace_with_retry(tmp, filename, tries=5):
    """Google Drive for desktop can briefly lock a file while syncing it."""
    for i in range(tries):
        try:
            os.replace(tmp, filename)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(3)


# ───────────────────────── HELPERS ─────────────────────────
def normalize_link(href):
    """Absolute URL without query/fragment, no trailing slash — stable dedup key."""
    parts = urlsplit(urljoin(BASE_URL, href))
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def org_from_link(link):
    """Fallback: organisation slug in /civil-society-directory/<slug>/calls/..."""
    m = re.search(r"/civil-society-directory/([^/]+)/", link or "")
    if not m:
        return "See link", "To Verify"
    return m.group(1).replace("-", " ").title(), "From link"


def _has(text, words):
    t = text.lower()
    for w in words:
        if re.fullmatch(r"[a-z\- ]+", w):  # English: whole-word match
            if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", t):
                return True
        elif w in t:  # Arabic: substring match
            return True
    return False


def classify(title):
    """Returns (Scope, Relevant, Status_override)."""
    if _has(title, CANCEL_WORDS):
        return "Cancelled", "No", "Cancelled"
    works = _has(title, WORKS_KEYWORDS)
    focus = _has(title, FOCUS_SUPPLY_KEYWORDS)
    if works:
        scope = "Works"
    elif focus or _has(title, SUPPLY_WORDS):
        scope = "Supply"
    elif _has(title, SERVICE_WORDS):
        scope = "Services"
    else:
        scope = "Other"
    return scope, "Yes" if (works or focus) else "No", None


REF_AFTER_KEYWORD = re.compile(
    r"\b(?:ITB|ITT|RFQ|RFP|RFI|EOI|IFB|Tender|nbr\.?|No\.?)(?:\s+|\s*[:#]\s*)"
    r"([A-Z0-9][A-Z0-9/\-_.]*\d[A-Z0-9/\-_.]*)", re.IGNORECASE)
REF_PREFIX_SPACE = re.compile(r"\b([A-Z]{2,5}\s+\d[\d\-/.]{3,})")      # e.g. LBN 1010-128
REF_TOKEN = re.compile(r"#?\b([A-Z0-9][A-Z0-9/\-_.]*\d[A-Z0-9/\-_.]*)")  # e.g. TR-FIN.26-05, BTS331


def extract_reference(title):
    """Reference number from the title if one is visible; otherwise To Verify."""
    m = REF_AFTER_KEYWORD.search(title)
    if m and len(m.group(1).strip(".-/")) >= 4:
        return m.group(1).strip(".-/"), "From title"
    m = REF_PREFIX_SPACE.search(title)
    if m:
        return m.group(1).strip(".-/"), "From title"
    for tok in REF_TOKEN.findall(title):
        tok = tok.strip(".-/")
        has_alpha_or_sep = bool(re.search(r"[A-Z/\-.]", tok))
        if (has_alpha_or_sep and len(tok) >= 5) or (tok.isdigit() and len(tok) >= 7):
            if not re.fullmatch(r"20\d\d", tok):
                return tok, "From title"
    return "", "To Verify"


def parse_date(text):
    text = re.sub(r"\s+", " ", (text or "").strip())
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def enrich(rec):
    """(Re)compute derived columns from Title / Deadline. Safe to run on old records."""
    title = rec.get("Title", "")
    scope, relevant, status = classify(title)
    # Title says nothing useful but the description mentions works → flag for a look
    if relevant == "No" and not status and _has(rec.get("Summary", ""), WORKS_KEYWORDS):
        relevant = "Check"
    rec["Scope"], rec["Relevant"] = scope, relevant
    if status:
        rec["Status"] = status

    if rec.get("Organization") in (None, "", "See link"):
        rec["Organization"], rec["Org_Check"] = org_from_link(rec.get("Link", ""))
    rec.setdefault("Org_Check", "OK")

    if not rec.get("Reference") or rec.get("Reference_Check") == "To Verify":
        rec["Reference"], rec["Reference_Check"] = extract_reference(title)

    d = parse_date(rec.get("Deadline")) or parse_date(rec.get("Deadline_Raw"))
    if "Deadline_Raw" not in rec:
        rec["Deadline_Raw"] = rec.get("Deadline", "")
    labelled = rec.get("Deadline_Check") == "OK"
    if d:
        rec["Deadline"] = d.isoformat()
        rec["Days_Left"] = (d - datetime.now().date()).days
        if labelled:
            rec["Deadline_Check"] = "OK"
        elif rec["Days_Left"] >= 0:
            rec["Deadline_Check"] = "Likely (unlabelled)"
        else:
            rec["Deadline_Check"] = "To Verify"
        if rec["Days_Left"] < 0 and rec.get("Status") == "New":
            rec["Status"] = "Expired"
    else:
        rec["Days_Left"] = ""
        rec["Deadline_Check"] = "To Verify"
    return rec


def find_cards(soup):
    for tag, cls in CARD_SELECTORS:
        cards = soup.find_all(tag, class_=cls)
        if cards:
            return cards
    return []


def looks_blocked(response):
    if response.status_code in (403, 429, 503):
        return True
    head = response.text[:3000].lower()
    return "just a moment" in head or "cf-challenge" in head or "challenge-platform" in head


# ───────────────────────── SESSION ─────────────────────────
def detect_chrome_major():
    """Installed Chrome major version (e.g. 155) from the Windows registry, else None.
    Without it, undetected_chromedriver downloads the NEWEST driver, which fails
    whenever Chrome on this PC has not updated yet ("only supports Chrome version 156")."""
    try:
        import winreg
    except ImportError:                       # cloud (Linux, GitHub Actions): ask Chrome itself
        import subprocess
        for exe in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            try:
                out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20).stdout
                m = re.search(r"(\d+)\.", out)
                if m:
                    return int(m.group(1))
            except (OSError, subprocess.SubprocessError):
                continue
        return None
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon", "version"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Google Chrome", "DisplayVersion"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Google Chrome", "DisplayVersion"),
    ]
    for hive, path, name in keys:
        try:
            with winreg.OpenKey(hive, path) as k:
                ver = str(winreg.QueryValueEx(k, name)[0])
            major = int(ver.split(".")[0])
            if major > 0:
                return major
        except (OSError, ValueError):
            continue
    return None


def get_browser_session(target_url):
    print(f"\nOpening browser to load: {target_url}")
    options = uc.ChromeOptions()
    if os.environ.get("DALEEL_CLOUD") == "1":    # GitHub Actions: real (non-headless) Chrome inside xvfb
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1280,900")
    kwargs = {"options": options}
    major = CHROME_MAJOR or detect_chrome_major()
    if major:
        kwargs["version_main"] = major
        print(f"Chrome version {major} detected - using matching driver.")
    else:
        print("WARNING: Chrome version not detected - set CHROME_MAJOR in tender_scraper.py if the browser fails to start.")
    driver = uc.Chrome(**kwargs)
    try:
        driver.get(target_url)
        # Wait until the listing actually renders instead of a fixed sleep
        start = time.time()
        while time.time() - start < BROWSER_WAIT_MAX:
            html = driver.page_source
            if any(cls in html for _, cls in CARD_SELECTORS):
                break
            time.sleep(2)
        else:
            print("WARNING: listing did not appear in the browser — session may not work.")

        cookies = driver.get_cookies()
        user_agent = driver.execute_script("return navigator.userAgent;")
    finally:
        driver.quit()

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain"))
    print("Browser closed, session ready.\n")
    return session


def fetch(session, url):
    r = session.get(url, timeout=REQUEST_TIMEOUT)
    if looks_blocked(r):
        raise BlockedError(f"Blocked/challenge page at {url} (HTTP {r.status_code})")
    r.raise_for_status()
    return r.text


# ───────────────────────── PARSING ─────────────────────────
def parse_card(card, category_label):
    text = card.get_text(" ", strip=True)

    if re.search(r"\bexpired\b", text, re.IGNORECASE):
        return None, "expired"

    title_box = (card.find("div", class_="field-name-title-field")
                 or card.find("div", class_="views-field-title")
                 or card.find(["h2", "h3"]))
    link_tag = title_box.find("a") if title_box else None
    if not link_tag:
        link_tag = card.find("a", href=True)
    if not link_tag or not link_tag.get("href"):
        return None, "nolink"

    # Deadline: Daleel Madani's labelled field, with an exact ISO date in "content"
    deadline, check = None, "OK"
    dl_field = card.find("div", class_="field-name-field-application-deadline")
    dl_span = dl_field.find("span", class_="date-display-single") if dl_field else None
    if dl_span:
        iso = (dl_span.get("content") or "")[:10]
        deadline = iso if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso) else dl_span.get_text(strip=True)
        deadline_raw = dl_span.get_text(strip=True)
    else:
        d = (card.find("span", class_="date-display-single")
             or card.find("div", class_="field-type-datetime"))
        deadline = d.get_text(strip=True) if d else "Check link"
        deadline_raw = deadline
        check = "To Verify"  # not in the deadline field — could be another date

    # Organisation: the group-reference field (schema:sourceOrganization)
    org_field = (card.find("div", class_="field-name-og-group-ref")
                 or card.find(attrs={"property": "schema:sourceOrganization"}))
    org = org_field.get_text(" ", strip=True) if org_field else ""
    org_check = "OK" if org else "To Verify"
    link = normalize_link(link_tag["href"])
    if not org:
        org, org_check = org_from_link(link)

    body = card.find("div", class_="field-name-body")
    summary = body.get_text(" ", strip=True)[:500] if body else ""

    title = link_tag.get_text(strip=True)
    return enrich({
        "Scraped_At": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "Status": "New",
        "Category": category_label,
        "Organization": org,
        "Org_Check": org_check,
        "Title": title,
        "Summary": summary,
        "Deadline": deadline,
        "Deadline_Raw": deadline_raw,
        "Deadline_Check": check,
        "Link": link,
    }), "ok"


# ───────────────────────── MAIN SCRAPE ─────────────────────────
def scrape_all():
    existing = load_existing_tenders()
    known = {normalize_link(t["Link"]) for t in existing if t.get("Link")}
    by_link = {normalize_link(t["Link"]): t for t in existing if t.get("Link")}
    print(f"Loaded {len(existing)} saved tenders.")

    new_tenders, errors = [], []
    session = None

    for path, label in CATEGORIES.items():
        print(f"\n{'=' * 40}\n--- {label} ---\n{'=' * 40}")
        empty_streak = 0
        refreshed = False

        for page in range(MAX_PAGES):
            url = f"{BASE_URL}{path}?page={page}"
            try:
                if session is None:
                    session = get_browser_session(url)
                html = fetch(session, url)
            except BlockedError as e:
                if refreshed:
                    errors.append(str(e))
                    print(f"STOP: {e}")
                    break
                print(f"{e} — refreshing session once.")
                session, refreshed = None, True
                try:
                    session = get_browser_session(url)
                    html = fetch(session, url)
                except Exception as e2:
                    errors.append(str(e2))
                    print(f"STOP: {e2}")
                    break
            except requests.RequestException as e:
                errors.append(f"{url}: {e}")
                print(f"STOP (network): {e}")
                break

            if DEBUG_SAVE_HTML and page == 0:
                debug_name = f"debug_{path.rstrip('/').split('/')[-1]}.html"
                with open(debug_name, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"Saved {debug_name} for selector checking.")

            cards = find_cards(BeautifulSoup(html, "html.parser"))
            if not cards:
                print(f"Page {page + 1}: no cards — end of listing.")
                break

            new_here = 0
            for card in cards:
                item, state = parse_card(card, label)
                if state != "ok":
                    continue
                if item["Link"] in known:
                    # Backfill exact page data into older records (keeps their Status)
                    old_rec = by_link.get(item["Link"])
                    if old_rec is not None:
                        for k in ("Organization", "Org_Check", "Summary",
                                  "Deadline", "Deadline_Raw", "Deadline_Check"):
                            if item.get(k):
                                old_rec[k] = item[k]
                    continue
                new_tenders.append(item)
                known.add(item["Link"])
                new_here += 1

            print(f"Page {page + 1}: {new_here} new.")
            empty_streak = empty_streak + 1 if new_here == 0 else 0
            if empty_streak >= STOP_AFTER_EMPTY_PAGES:
                print("No new items for several pages — stopping this category.")
                break
            time.sleep(PAGE_DELAY)
        else:
            print(f"Reached MAX_PAGES ({MAX_PAGES}).")

    return existing, new_tenders, errors


if __name__ == "__main__":
    old, new, errors = scrape_all()

    # Re-score old records too, so keyword/date fixes apply to the whole register
    combined = [enrich(t) for t in new] + [enrich(t) for t in old]
    if combined:
        save_tenders(combined)
        export_csv(combined)

    if new:
        rel = sum(1 for t in new if t["Relevant"] == "Yes")
        tv = sum(1 for t in new if t["Deadline_Check"] == "To Verify")
        print(f"\nSaved {len(new)} new items ({rel} relevant, {tv} deadlines To Verify).")
    else:
        print("\nNo new tenders found.")
    if combined:
        open_relevant = sum(1 for t in combined
                            if t["Relevant"] == "Yes" and t.get("Status") == "New")
        print(f"Register: {len(combined)} total, {open_relevant} relevant and open.")
        print(f"Files: {DATA_FILE}, {CSV_FILE}")

    if errors:
        print("\nRUN INCOMPLETE — errors:")
        for e in errors:
            print(" -", e)
