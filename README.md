# Valeo tender scraper (cloud)

Runs every hour on GitHub Actions — no PC needed.

1. `tender_scraper.py` reads the Daleel Madani listings (real Chrome in a virtual screen).
2. `tender_documents.py` downloads each new tender's documents.
3. `cloud_upload.py` sends the folders, the CSV and `last_run.txt` to Google Drive **through the tender bot** (Apps Script web app). No Google password or key is stored here.

Secrets (Settings → Secrets and variables → Actions): `BOT_URL` (the bot's /exec URL), `SCRAPER_KEY` (upload key, same as the bot's Script Property).

`daleel_madani_tenders.json` is the history (public tender data only); each run saves it back so nothing is downloaded twice.
Manual run: Actions → Daleel tenders (hourly) → Run workflow.
