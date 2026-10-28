"""
Daleel Madani — tender document downloader — v11
  v11: also looks in the "Medical" stage folder (medical tenders, bot v20).
  v10: tender folders live in four stage folders (Available Tenders / Bidding / Not Bidding / Expired);
       the WhatsApp bot moves them, this script finds them wherever they are and never downloads twice.
       E-mail addresses hidden by Daleel's anti-spam ("[email protected]") are decoded.
       DALEEL_REFRESH_PAGES=1 rewrites _tender_page.txt / _links.txt of open tenders (no file downloads).
  v9:  folder name starts with the CLOSING date: "YYMMDD Client - Title"
Reads daleel_madani_tenders.json (made by tender_scraper.py), opens every open tender,
and saves into one folder per tender:
    <YYMMDD> <Client> - <Title>      (YYMMDD = closing date / deadline; "TBD" if unknown)
        _tender_page.txt      full text of the tender announcement
        _links.txt            every external / document link found (Drive, Kobo, UNGM, email…)
        <attachments>         PDF / Word / Excel / ZIP files hosted on the page
Updates the JSON/CSV with Docs_Status, Docs_Count, Docs_Folder, External_Links.

Run:  python tender_documents.py
Put tender_documents.py in the same folder as tender_scraper.py.
"""

import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlsplit, unquote

import requests
from bs4 import BeautifulSoup

import tender_scraper as ts

# ───────────────────────── CONFIG ─────────────────────────
# Tip: point this at your Google Drive for desktop folder (e.g. r"G:\My Drive\Tenders")
# and every tender folder syncs to Drive automatically.
DOCS_ROOT = os.environ.get("DALEEL_DOCS", r"Available Tenders")   # new tenders go here (set by run_tenders.bat)
OLD_ROOT_NAME = "Tenders"                                          # v9 name, renamed once to DOCS_ROOT
STAGE_NAMES = ("Bidding", "Not Bidding", "Expired", "Medical")     # siblings of DOCS_ROOT, managed by the bot (v11: + Medical)
REFRESH_PAGES = os.environ.get("DALEEL_REFRESH_PAGES", "0") == "1"

ONLY_RELEVANT = os.environ.get("DALEEL_ONLY_RELEVANT", "0") == "1"   # 1 = only Relevant Yes/Check
SKIP_STATUSES = {"Expired", "Cancelled", "Dropped"}
REDOWNLOAD = False           # True = re-check tenders already marked Downloaded
MAX_FILE_MB = 100
DETAIL_DELAY = 2             # seconds between tenders
DEBUG_SAVE_FIRST_DETAIL = False   # saves debug_detail.html for selector checking

DOC_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".csv", ".zip", ".rar",
           ".7z", ".ppt", ".pptx", ".dwg", ".jpg", ".jpeg", ".png", ".txt", ".rtf", ".odt", ".ods")
HOSTED_PATHS = ("/sites/default/files/", "/system/files/", "/download/")
EXTERNAL_HINTS = ("drive.google.", "docs.google.", "dropbox.", "onedrive.", "sharepoint.",
                  "1drv.ms", "wetransfer", "kobo", "ungm.org", "forms.gle", "forms.office",
                  "tenders", "procurement", "eprocurement")

WIN_BAD = r'<>:"/\|?*'


# ───────────────────────── HELPERS ─────────────────────────
def clean_name(text, max_len):
    text = "".join("-" if ch in WIN_BAD else ch for ch in (text or ""))
    text = re.sub(r"[\x00-\x1f]", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    return text[:max_len].rstrip(" .-") or "NA"


def folder_name(rec):
    """e.g. 261026 World Vision - Kitchen rehabilitation works  (261026 = closing date).
    The Tender Tracker reads this date as the deadline, so it must never be a guess:
    no valid deadline -> "TBD" (the tracker then leaves the deadline To Verify)."""
    try:
        stamp = datetime.strptime(str(rec.get("Deadline", ""))[:10], "%Y-%m-%d").strftime("%y%m%d")
    except ValueError:
        stamp = "TBD"
    client = clean_name(rec.get("Organization") or "Client To Verify", 40)
    title = clean_name(rec.get("Title") or "Untitled", 60)   # keeps full paths under Windows' 260-character limit
    return f"{stamp} {client} - {title}"


def unique_dir(root, name):
    """Same client + same title on the same day (e.g. Lot 1 / Lot 2) -> add (2), (3)…"""
    path = os.path.join(root, name)
    i = 2
    while os.path.exists(path):
        path = os.path.join(root, f"{name} ({i})")
        i += 1
    return path


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def stage_dirs():
    """Available Tenders first, then Bidding / Not Bidding / Expired (same parent folder)."""
    parent = os.path.dirname(os.path.abspath(DOCS_ROOT))
    return [DOCS_ROOT] + [os.path.join(parent, n) for n in STAGE_NAMES]


def find_folder(name):
    """Full path of an existing tender folder, whichever stage folder the bot moved it to."""
    if not name:
        return None
    for d in stage_dirs():
        p = os.path.join(d, name)
        if os.path.isdir(p):
            return p
    return None


def unique_dir_all(root, name):
    """Like unique_dir, but the name must be free in every stage folder (a moved folder keeps its name)."""
    i, cand = 2, name
    while find_folder(cand):
        cand = f"{name} ({i})"
        i += 1
    return os.path.join(root, cand)


def migrate_old_root():
    """One time: rename the v9 'Tenders' folder to 'Available Tenders' (Drive keeps the folder ID)."""
    old = os.path.join(os.path.dirname(os.path.abspath(DOCS_ROOT)), OLD_ROOT_NAME)
    if os.path.isdir(old) and not os.path.isdir(DOCS_ROOT):
        try:
            os.rename(old, DOCS_ROOT)
            print(f"Renamed folder '{OLD_ROOT_NAME}' -> '{os.path.basename(DOCS_ROOT)}'.")
        except OSError as e:
            print(f"  ! could not rename '{old}': {e}")


def decode_cf_email(hexstr):
    """Daleel (Cloudflare) hides e-mails as hex: first byte is the key, the rest XOR the key."""
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return ""


def unhide_emails(soup):
    """Replaces Cloudflare-protected e-mail links/spans with the real address, as text and mailto link."""
    for el in soup.select("[data-cfemail]"):
        addr = decode_cf_email(el.get("data-cfemail", ""))
        if addr:
            el.replace_with(addr)
    for a in soup.find_all("a", href=True):
        m = re.search(r"/cdn-cgi/l/email-protection#([0-9a-fA-F]+)", a["href"])
        if m:
            addr = decode_cf_email(m.group(1))
            if addr:
                a["href"] = "mailto:" + addr
                if "protected" in a.get_text().lower() or not a.get_text(strip=True):
                    a.string = addr


def filename_from_response(resp, url):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    name = unquote(m.group(1)) if m else unquote(os.path.basename(urlsplit(url).path))
    return clean_name(name, 100) or "document"


def is_document_link(href):
    path = urlsplit(href).path.lower()
    return path.endswith(DOC_EXT) or any(p in path for p in HOSTED_PATHS)


def page_main(soup):
    """Main content block of a Drupal node page (falls back to <body>)."""
    return (soup.find("div", class_=re.compile(r"\bnode-call-for-proposal\b"))
            or soup.find("article")
            or soup.find("div", id="content")
            or soup.find("main")
            or soup.body or soup)


def extract_detail(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    unhide_emails(soup)
    main = page_main(soup)
    for tag in main.find_all(["script", "style", "nav"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", main.get_text("\n", strip=True))

    docs, external, emails = [], [], set()
    seen = set()
    for a in main.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("mailto:"):
            emails.add(href[7:].split("?")[0])
            continue
        full = urljoin(page_url, href)
        if full in seen or full.startswith("javascript:") or "#" == href[:1]:
            continue
        seen.add(full)
        host = urlsplit(full).netloc.lower()
        label = a.get_text(" ", strip=True)
        if is_document_link(full) and "daleel-madani.org" in host:
            docs.append((full, label))
        elif "daleel-madani.org" not in host:
            if is_document_link(full) or any(h in full.lower() for h in EXTERNAL_HINTS):
                external.append((full, label))
    # e-mails written as plain text (submission / request-documents addresses)
    emails.update(e.rstrip(".") for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text))
    return text, docs, external, sorted(e for e in emails if e)


def download(session, url, folder):
    with session.get(url, timeout=ts.REQUEST_TIMEOUT, stream=True) as r:
        if ts.looks_blocked(r):
            raise ts.BlockedError(f"Blocked while downloading {url}")
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "").lower()
        if "text/html" in ctype:
            raise ValueError("server returned a web page, not a file")
        size = int(r.headers.get("Content-Length") or 0)
        if size > MAX_FILE_MB * 1024 * 1024:
            raise ValueError(f"file larger than {MAX_FILE_MB} MB")
        path = unique_path(os.path.join(folder, filename_from_response(r, url)))
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
        os.replace(tmp, path)
        return os.path.basename(path)


def rename_to_deadline(records):
    """Keep every folder's date prefix equal to the current Daleel deadline.
    Runs on every pass, so an addendum that moves the deadline renames the folder too.
    Renaming keeps the Google Drive folder ID, so the Tender Tracker row stays linked."""
    renamed = 0
    for rec in records:
        old = rec.get("Docs_Folder")
        if not old:
            continue
        new = folder_name(rec)
        if old == new or old.startswith(new + " ("):
            continue
        src = find_folder(old)
        if not src:
            continue
        dst = unique_dir_all(os.path.dirname(src), new)   # rename in place, in whichever stage folder it is
        try:
            os.rename(src, dst)
        except OSError as e:
            print(f"  ! could not rename '{old}': {e}")
            continue
        rec["Docs_Folder"] = os.path.basename(dst)
        renamed += 1
        print(f"  renamed: {old}  ->  {rec['Docs_Folder']}")
    if renamed:
        print(f"{renamed} folder(s) renamed to their closing date.")
        ts.save_tenders(records)
    return renamed


def should_process(rec):
    if rec.get("Status") in SKIP_STATUSES:
        return False
    if isinstance(rec.get("Days_Left"), int) and rec["Days_Left"] < 0:
        return False
    if ONLY_RELEVANT and rec.get("Relevant") not in ("Yes", "Check"):
        return False
    if REFRESH_PAGES:
        return bool(rec.get("Docs_Folder"))
    if not REDOWNLOAD and rec.get("Docs_Status") in ("Downloaded", "No attachments", "External only"):
        return False
    return True


# ───────────────────────── MAIN ─────────────────────────
def main():
    records = [ts.enrich(r) for r in ts.load_existing_tenders()]
    migrate_old_root()
    if any(os.path.isdir(d) for d in stage_dirs()):
        rename_to_deadline(records)
    todo = [r for r in records if should_process(r)]
    print(f"{len(records)} tenders in register, {len(todo)} to process.")
    if not todo:
        return

    os.makedirs(DOCS_ROOT, exist_ok=True)
    listing_url = f"{ts.BASE_URL}{next(iter(ts.CATEGORIES))}?page=0"
    session = ts.get_browser_session(listing_url)
    refreshed = False
    debug_saved = False
    totals = {"files": 0, "tenders_ok": 0, "failed": 0}

    for n, rec in enumerate(todo, 1):
        print(f"\n[{n}/{len(todo)}] {rec.get('Title', '')[:70]}")
        try:
            try:
                html = ts.fetch(session, rec["Link"])
            except ts.BlockedError:
                if refreshed:
                    raise
                print("  Blocked — refreshing session once.")
                session, refreshed = ts.get_browser_session(listing_url), True
                html = ts.fetch(session, rec["Link"])

            if DEBUG_SAVE_FIRST_DETAIL and not debug_saved:
                with open("debug_detail.html", "w", encoding="utf-8") as f:
                    f.write(html)
                debug_saved = True

            text, docs, external, emails = extract_detail(html, rec["Link"])

            if rec.get("Docs_Folder"):   # already has a folder from an earlier run -> reuse it, wherever the bot moved it
                folder = find_folder(rec["Docs_Folder"]) or os.path.join(DOCS_ROOT, rec["Docs_Folder"])
            else:                        # new tender -> new folder in Available Tenders, never merged with another tender
                folder = unique_dir_all(DOCS_ROOT, folder_name(rec))
            os.makedirs(folder, exist_ok=True)
            rec["Docs_Folder"] = os.path.basename(folder)

            with open(os.path.join(folder, "_tender_page.txt"), "w", encoding="utf-8") as f:
                f.write(f"{rec.get('Title', '')}\n{rec.get('Organization', '')}\n"
                        f"Deadline: {rec.get('Deadline', '')}\nLink: {rec['Link']}\n"
                        f"Saved: {datetime.now():%Y-%m-%d %H:%M}\n\n{text}\n")

            got, failed = [], []
            if REFRESH_PAGES:            # keep the list of files already in the folder
                got = sorted(f for f in os.listdir(folder) if not f.startswith("_") and not f.endswith(".part"))
            for url, label in ([] if REFRESH_PAGES else docs):
                try:
                    name = download(session, url, folder)
                    got.append(name)
                    print(f"  ✓ {name}")
                except Exception as e:
                    failed.append(f"{url}  ({e})")
                    print(f"  ✗ {url} — {e}")

            with open(os.path.join(folder, "_links.txt"), "w", encoding="utf-8") as f:
                f.write("DOWNLOADED\n" + ("\n".join(got) or "-") + "\n\n")
                f.write("FAILED — download manually\n" + ("\n".join(failed) or "-") + "\n\n")
                f.write("EXTERNAL LINKS — open manually\n"
                        + ("\n".join(f"{u}  [{l}]" for u, l in external) or "-") + "\n\n")
                f.write("E-MAILS IN ANNOUNCEMENT (document requests / submission)\n"
                        + ("\n".join(emails) or "-") + "\n")

            if REFRESH_PAGES:            # page text and e-mails refreshed only; files and status unchanged
                rec["External_Links"] = " | ".join(u for u, _ in external)
                rec["Contact_Emails"] = ", ".join(emails)
                totals["tenders_ok"] += 1
                print(f"  → page refreshed, e-mails: {', '.join(emails) or '-'}")
                ts.save_tenders(records)
                time.sleep(DETAIL_DELAY)
                continue
            rec["Docs_Count"] = len(got)
            rec["External_Links"] = " | ".join(u for u, _ in external)
            rec["Contact_Emails"] = ", ".join(emails)
            if failed:
                rec["Docs_Status"] = "Partial - To Verify"
            elif got:
                rec["Docs_Status"] = "Downloaded"
            elif external or emails:
                rec["Docs_Status"] = "External only"
            else:
                rec["Docs_Status"] = "No attachments"
            totals["files"] += len(got)
            totals["tenders_ok"] += 1
            print(f"  → {rec['Docs_Status']}: {len(got)} file(s), {len(external)} external link(s)")

        except ts.BlockedError as e:
            print(f"STOP: {e}. Progress so far is saved; run again later.")
            break
        except Exception as e:
            rec["Docs_Status"] = "Failed - To Verify"
            totals["failed"] += 1
            print(f"  ✗ failed: {e}")

        # save after every tender so nothing is lost if the run stops
        ts.save_tenders(records)
        time.sleep(DETAIL_DELAY)

    export_with_docs(records)
    print(f"\nDone: {totals['tenders_ok']} tenders processed, {totals['files']} files downloaded, "
          f"{totals['failed']} failed.  Folder: {os.path.abspath(DOCS_ROOT)}")
    need = [r for r in records if str(r.get("Docs_Status", "")).endswith("To Verify")
            or r.get("Docs_Status") == "External only"]
    if need:
        print(f"{len(need)} tenders need a manual step (external link, e-mail request or failed file) "
              f"— see _links.txt in each folder.")


def export_with_docs(records):
    ts.export_csv(records)  # same columns as the scraper, incl. Docs_* fields


if __name__ == "__main__":
    main()
