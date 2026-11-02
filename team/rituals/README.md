# Rituals

Times are Lebanon time on the 12-hour clock. These are **proposals**: none of them is scheduled until the owner approves it.
Scheduled runs only read and write reports or the board. They never submit, send, pay or change settings.

| When | Ritual | Who | Output |
|---|---|---|---|
| 7:45 AM, Mon–Sat | Stand-up | every department | One line each on the board (event `note`): yesterday / today / blocked |
| 8:00 AM, Mon–Sat | Morning brief | CEO | `days/<date>.md`: see below |
| 8:15 AM, Mon–Sat | Morning sweep | scout | New tenders logged as `ready` tasks |
| 6:30 PM, Mon–Sat | Wrap-up | CEO | Appended to `days/<date>.md`: done, slipped, tomorrow's deadlines |
| Fri, 3:00 PM | Study hour | every department | One skill/tool proposal each (see `levels.md`) |
| Sun, 8:30 PM | Weekly meeting | CEO | `days/week-<YYYY-WW>.md`: wins, misses, bids won/lost, fixes for next week, XP awarded; prune events older than 30 days |
| 1st of the month | Monthly review | CEO | Bids submitted vs. won, hit rate, average review/pricing time, each department's level |
| 3:30 AM, daily | Night check | CEO | Scraper last run OK? Any task stuck >2 days? Any closing date within 72h without a "ready to submit"? Results go to the board as a `stuck` event, or nothing if all clear |

## Morning brief template (CEO, 8:00 AM)

```
Every day at 8:00 AM Lebanon time:
Read the board: tasks, open needs, and yesterday's wrap-up.
Write days/<today's date>.md with:
1. What the owner must do today (3 things at most), most urgent first, with closing dates.
2. Each department's plan, one line each.
3. Deadlines in the next 7 days.
4. A fallback plan for a hard day (what can slip, what can't).
Not allowed: submitting, sending, paying, or changing any setting.
```

## Wrap-up template (CEO, 6:30 PM)

```
Read today's events and tasks. Append to days/<today's date>.md:
- Done today (one line per item, with a link)
- Slipped, and why
- Tomorrow: deadlines and the first task for each department
- Open needs still waiting for the owner
```
