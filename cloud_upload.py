"""
Cloud upload — v1. Runs after tender_scraper.py and tender_documents.py on GitHub Actions.

Sends every tender folder made in this run to Google Drive through the tender bot (Apps Script web app),
then the CSV and last_run.txt. Needs two environment variables (GitHub secrets):
    BOT_URL      the bot's web app URL (…/exec)
    SCRAPER_KEY  the upload key (also stored in the bot's Script Properties)
The bot finds the folder wherever it is (Available Tenders / Bidding / Not Bidding / Expired / Medical) and never
uploads a file twice. A folder that fails to upload is marked "Failed - To Verify" so the next run tries again.
"""

import base64
import json
import mimetypes
import os
import sys
import time
from datetime import datetime

import requests

import tender_scraper as ts

BOT_URL = os.environ.get("BOT_URL", "").strip()
KEY = os.environ.get("SCRAPER_KEY", "").strip()
OUT = os.environ.get("DALEEL_OUT", "out")
DOCS_ROOT = os.environ.get("DALEEL_DOCS", os.path.join(OUT, "Available Tenders"))
CSV_PATH = os.environ.get("DALEEL_CSV", os.path.join(OUT, "daleel_madani_tenders.csv"))
MAX_UPLOAD_MB = 25          # Apps Script request limit (base64 adds a third)
OVERWRITE = {"_tender_page.txt", "_links.txt"}


def call(action, payload, tries=3):
    for i in range(tries):
        try:
            r = requests.post(BOT_URL, params={"key": KEY, "action": action}, data=json.dumps(payload),
                              headers={"Content-Type": "application/json"}, timeout=300)
            j = r.json()
            if j.get("error"):
                raise RuntimeError(j["error"])
            return j
        except Exception as e:  # noqa: BLE001 – network, JSON or bot error: retry, then give up
            if i == tries - 1:
                raise
            print(f"   retry {action}: {e}")
            time.sleep(5 * (i + 1))


def send_file(path, name, folder_id=None, root=False, overwrite=False):
    with open(path, "rb") as f:
        data = f.read()
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    p = {"name": name, "b64": base64.b64encode(data).decode(), "mime": mime, "overwrite": overwrite}
    p.update({"root": True} if root else {"folder_id": folder_id})
    return call("file", p)


def main():
    if not BOT_URL or not KEY:
        sys.exit("BOT_URL / SCRAPER_KEY missing (GitHub secrets).")
    call("ping", {})
    records = ts.load_existing_tenders()
    by_folder = {r.get("Docs_Folder"): r for r in records if r.get("Docs_Folder")}
    done = failed = files = 0
    local = sorted(d for d in os.listdir(DOCS_ROOT)) if os.path.isdir(DOCS_ROOT) else []
    print(f"{len(local)} tender folder(s) to upload.")
    for name in local:
        path = os.path.join(DOCS_ROOT, name)
        rec = by_folder.get(name)
        try:
            res = call("folder", {"name": name, "link": (rec or {}).get("Link", "")})
            if rec is not None and res["name"] != name:
                rec["Docs_Folder"] = res["name"]          # another tender already had that name in Drive
            big = []
            names = sorted(os.listdir(path), key=lambda n: (n in OVERWRITE, n))   # page text and links last
            for fn in names:
                fp = os.path.join(path, fn)
                if fn.endswith(".part") or not os.path.isfile(fp):
                    continue
                if os.path.getsize(fp) > MAX_UPLOAD_MB * 1024 * 1024:
                    big.append(fn)
                    continue
                if fn == "_links.txt" and big:
                    with open(fp, "a", encoding="utf-8") as f:
                        f.write("\nTOO LARGE FOR THE CLOUD UPLOAD — download from the Daleel page\n" + "\n".join(big) + "\n")
                    if rec is not None:
                        rec["Docs_Status"] = "Partial - To Verify"
                r = send_file(fp, fn, folder_id=res["id"], overwrite=fn in OVERWRITE)
                files += 0 if r.get("skipped") else 1
            done += 1
            print(f" ✓ {res['name']}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f" ✗ {name}: {e}")
            if rec is not None:
                rec["Docs_Status"] = "Failed - To Verify"   # downloaded again next run
    ts.save_tenders(records)
    ts.export_csv(records, CSV_PATH)
    send_file(CSV_PATH, os.path.basename(CSV_PATH), root=True, overwrite=True)
    status = "OK" if not failed else "CHECK LOG"
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    lr = os.path.join(OUT, "last_run.txt")
    with open(lr, "w", encoding="utf-8") as f:
        f.write(f"{stamp}  {status}  cloud (GitHub)  folders={done}  failed={failed}  files={files}\n")
    send_file(lr, "last_run.txt", root=True, overwrite=True)
    print(f"Upload done: {done} folder(s), {files} new file(s), {failed} failed.")


if __name__ == "__main__":
    main()
