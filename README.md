# South Florida Spillway Alerts

Automated, 10-minute South Florida spillway monitoring for fishing. A Cloudflare Worker cron trigger launches GitHub Actions, which checks [MacVicar Consulting's mobile readings](https://www.macvicarconsulting.com/readings/readingsmobil.htm), keeps state/history in this repository, and publishes alerts to an ntfy topic.

## Monitored structures (11)

| Code | Area |
| --- | --- |
| S80 | Stuart Locks |
| S99 | Fort Pierce |
| S49 | Port St. Lucie |
| S97 | Palm City |
| S46 | Jupiter |
| S44 | North Palm Beach |
| S155 | Lake Worth |
| S41 | Boynton Beach |
| S40 | Delray Beach |
| G56 | Boca Raton (MacVicar label) |
| S37A | Pompano Beach |

## Notifications

- **OPENED/CLOSED:** Immediate notification on a real transition between zero and nonzero CFS. The first observation of a newly added site establishes a baseline without inventing an opening.
- **INCREASED/DECREASED:** Other than S37A, sends an alert once flow has moved by **100 CFS or more, cumulatively** since the last alerted baseline. Changes under 100 CFS accumulate; an opening/closing resets that baseline.
- **S46 Jupiter priority tiers:** On an opening **below 100 CFS**, send a normal **STARTING TO OPEN** alert. Send a **SUPER ALERT** (maximum priority) once flow first reaches **100 CFS** during that opening, including if it opens at or above 100 CFS. Later cumulative increases of 100+ CFS also remain SUPER ALERTS. Closings and decreases are not SUPER ALERTS.
- **S37A Pompano Beach:** Sends **only OPENED/CLOSED** notifications, at **default** ntfy priority rather than high. It still tracks transitions but sends no flow-change or predictive warnings.
- **S49 Port St. Lucie:** Closing messages include the custom Shadathon line.

## Predictive opening/closing alerts

- Separate models learn from the upstream water levels observed at each site's real openings and closings.
- A prediction is enabled after **at least five** recorded events of that type for the site.
- Uses the **median** upstream level for each event type to reduce sensitivity to outliers.
- **MAY OPEN SOON:** When the site is closed and upstream is between the historical opening median minus **0.10 ft** and the median.
- **MAY CLOSE SOON:** When the site is open and upstream is between the historical closing median and the median plus **0.10 ft**.
- Only sends one prediction while readings remain continuously in the relevant zone; it can warn again if they leave and later re-enter.
- Prediction alerts are estimates, not guarantees. S37A is excluded.

New prediction notifications are recorded in `spillway_history.json` under `predictions` (in addition to actual `events`). Each is marked **correct** if the predicted transition happens within **24 hours**; otherwise it expires or is marked incorrect if the opposite event happens. A correct prediction also records its lead time in minutes. This prospective tracking was added October 24, 2026; historical prediction notification timestamps were not previously saved.

## Scheduling and operation

1. Cloudflare Worker scheduled trigger: `*/10 * * * *` (every 10 minutes, UTC-based).
2. The Worker dispatches `Spillway Alerts` using GitHub's `workflow_dispatch` API on `main`, with an Actions-enabled GitHub token stored as the Worker's encrypted `GITHUB_TOKEN` secret.
3. The [GitHub Actions workflow](.github/workflows/spillways.yml) executes `python monitor.py` using repository secret `NTFY_TOPIC`.
4. The monitor fetches MacVicar readings, compares with `state.json`, notifies subscribers via `https://ntfy.sh/<topic>`, and writes `state.json` and `spillway_history.json`.
5. The workflow commits updated state/history back to `main` when there are changes. GitHub concurrency cancels overlapping runs.

GitHub's native cron schedule is **not** used. New users subscribe via ntfy to the private topic name provided by the administrator; do not publish the topic name or either secret.

## Configuration

The main settings are near the top of [monitor.py](monitor.py):

- `CHANGE_THRESHOLD = 100` (CFS, cumulative).
- `S46_MAJOR_FLOW_CFS = 100` (CFS).
- `PREDICTION_DISTANCE_FT = 0.10`.
- `MIN_HISTORY_EVENTS = 5`.
- `PREDICTION_VALIDITY_HOURS = 24`.

Actual readings and outages depend on MacVicar availability; ntfy depends on network access to ntfy.sh. The monitor does not currently retry network failures automatically.
