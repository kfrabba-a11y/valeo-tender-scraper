# Board protocol

**Board:** https://claude.ai/artifact/1Sj3pfEGtvRfqo66H8wPyY
Sessions write to it with the **ArtifactData** tool, passing that URL as `url`. The owner reads it on the phone and answers needs there.

## Collections

### `staff/<staff id>`: one document per department
```json
{ "name": "Tender scout", "role": "Finds new tenders daily", "order": 2,
  "status": "working", "task": "Morning sweep: Daleel + UNGM", "note": "6 found, 2 fit",
  "level": 1, "xp": 0, "skills": [], "updated": "2026-11-02T08:10:00+02:00" }
```
`status` is exactly one of: `working`, `idle` (free), `waiting_owner`, `waiting_dept`, `stuck`, `meeting`, `learning`.
Update your status with `update` (merge). Never overwrite another department's document.

### `tasks/<id>`: one per tender or job
```json
{ "title": "UNICEF – WASH rehab, 3 schools, Akkar", "ref": "LRPS-2026-9187", "owner": "reviewer",
  "stage": "working", "deadline": "2026-11-20T12:00:00+02:00",
  "folder": "tenders/2026-11-02_unicef-wash-akkar", "note": "", "updated": "…" }
```
`stage` is one of: `ready`, `working`, `awaiting` (waiting for the owner), `blocked`, `done`.
Task id: `<YYYYMMDD>-<short-name>` (for example `20261102-unicef-wash-akkar`).

### `needs/<id>`: anything that needs the owner's decision
```json
{ "title": "Bid or no-bid: UNICEF WASH Akkar", "from": "ceo", "kind": "bid/no-bid",
  "detail": "Reviewer recommends BID: 1) fits our WASH record 2) value within limit 3) 18 days to close.\nRisk: 10% performance bond.",
  "link": "https://…/review.md", "status": "open", "created": "…" }
```
The owner answers on the board. The page sets `status` to `approved` or `declined` and adds `answer` (the owner's note, word for word), `decidedAt` and `decidedBy: "owner"`, and logs a `decision` event.
**Sessions never set `status` to approved or declined themselves.** To withdraw a need, set `status: "withdrawn"` and log why.

### `events/<id>`: the activity log
```json
{ "type": "report", "from": "scout", "text": "Morning sweep done: 6 new, 2 fit, 4 dropped", "at": "…" }
```
`type` is one of: `start`, `report`, `stuck`, `need`, `decision`, `note`. Add events with `set` on a fresh id: `<YYYYMMDD-HHMM>-<staff id>`.

## When to write

| Moment | Your staff doc | Also |
|---|---|---|
| Start a task | `status: working`, `task`, `note` (one line) | event `start` |
| Need the owner | `status: waiting_owner` | need card + event `need` |
| Waiting on another department | `status: waiting_dept`, `note`: which one | – |
| Stuck | `status: stuck`, `note`: why | event `stuck`; task stage `blocked` |
| Finished | `status: idle`, clear `task` | event `report` for the CEO; task stage updated |

## Rules
- Read the document before you write, and pass its `version` as `if_version` so you don't overwrite someone else's change.
- Times are ISO with the Lebanon offset in the data; the board shows them on the 12-hour clock.
- If a board write fails, carry on with the work and mention it in your report.
- Nothing private on the board: no bank details, client contacts, passwords or unpublished prices. Link to the file instead.
- The CEO deletes `events` older than 30 days every Sunday.
