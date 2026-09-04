# South Florida Spillway Alerts

Checks 10 coastal South Florida structures every 5 minutes using MacVicar Consulting's public readings page and sends ntfy push notifications when a flow threshold is crossed.

## Default structures

- S46 — Jupiter
- S44 — North Palm Beach
- S155 — Lake Worth
- S41 — Boynton Beach
- S40 — Delray Beach
- S37A — Pompano Beach
- S36 — Fort Lauderdale
- S26 — Miami
- S22 — Snapper Creek
- S20F — Biscayne area

All thresholds start at 1 CFS. Edit `SPILLWAYS` near the top of `monitor.py` to change them.

## GitHub setup

1. Create a PUBLIC GitHub repository.
2. Upload the contents of this package, preserving `.github/workflows/spillways.yml`.
3. Go to repository Settings → Secrets and variables → Actions.
4. Create a repository secret:
   - Name: `NTFY_TOPIC`
   - Value: your ntfy topic name
5. Go to Actions → Spillway Alerts → Run workflow once.
6. The first run creates the baseline and intentionally sends no threshold alerts.
7. Future crossings generate ntfy notifications.

GitHub scheduled workflows can be delayed occasionally; a 5-minute cron is not guaranteed to execute at the exact minute.

Public repositories using standard GitHub-hosted runners currently do not consume paid Actions minutes.
