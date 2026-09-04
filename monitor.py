import os
import re
import json
import html
import urllib.request
from pathlib import Path

SOURCE_URL = "https://www.macvicarconsulting.com/readings/readingsmobil.htm"
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
STATE_FILE = Path("state.json")

# Change thresholds here whenever you want.
# Alert fires only when a spillway CROSSES the threshold.
SPILLWAYS = {
    "S46":  {"name": "Jupiter",          "threshold": 1},
    "S44":  {"name": "N. Palm Beach",   "threshold": 1},
    "S155": {"name": "West Palm Beach", "threshold": 1},
    "S41":  {"name": "Boynton Beach",   "threshold": 1},
    "S40":  {"name": "Delray Beach",    "threshold": 1},
    "S37A": {"name": "Pompano Beach",   "threshold": 1},
    "S36":  {"name": "Fort Lauderdale", "threshold": 1},
    "S26":  {"name": "Miami",           "threshold": 1},
    "S22":  {"name": "Snapper Creek",   "threshold": 1},
    "S20F": {"name": "Biscayne area",   "threshold": 1},
}

def fetch_html():
    req = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "spillway-alert-monitor/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")

def strip_tags(value):
    value = re.sub(r"<script.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()

def parse_readings(page):
    readings = {}

    # Each MacVicar table row contains location, site, flow, upstream, downstream.
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S)

    for row in rows:
        text = strip_tags(row)

        for site in SPILLWAYS:
            # Site must appear as its own token, so S40 won't accidentally match S400.
            if not re.search(rf"\b{re.escape(site)}\b", text, flags=re.I):
                continue

            # Take the text starting at the site name, then grab the next 3 numeric values.
            m = re.search(rf"\b{re.escape(site)}\b(.*)", text, flags=re.I)
            if not m:
                continue

            nums = re.findall(r"-?\d+(?:\.\d+)?", m.group(1))
            if len(nums) >= 3:
                readings[site] = {
                    "flow": float(nums[0]),
                    "upstream": float(nums[1]),
                    "downstream": float(nums[2]),
                }

    return readings

def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

def ntfy(title, message, priority="high"):
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    data = message.encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Title": title.encode("ascii", errors="ignore").decode(),
            "Priority": priority,
            "Tags": "ocean,warning",
            "Content-Type": "text/plain; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()

def fmt_flow(x):
    return str(int(x)) if x.is_integer() else f"{x:g}"

def main():
    page = fetch_html()
    readings = parse_readings(page)

    missing = [s for s in SPILLWAYS if s not in readings]
    if missing:
        print("WARNING: Could not parse:", ", ".join(missing))

    old_state = load_state()
    new_state = dict(old_state)
    first_run = not bool(old_state)

    for site, cfg in SPILLWAYS.items():
        if site not in readings:
            continue

        r = readings[site]
        threshold = float(cfg["threshold"])
        above = r["flow"] >= threshold
        previous = old_state.get(site, {}).get("above")

        new_state[site] = {
            "above": above,
            "flow": r["flow"],
            "upstream": r["upstream"],
            "downstream": r["downstream"],
        }

        print(
            f"{site} {cfg['name']}: flow={r['flow']} "
            f"up={r['upstream']} down={r['downstream']} "
            f"threshold={threshold} above={above}"
        )

        # First run establishes baseline without blasting notifications.
        if first_run or previous is None:
            continue

        if above and not previous:
            ntfy(
                f"{site} {cfg['name']} IS FLOWING",
                f"{site} crossed {fmt_flow(threshold)} CFS.\n\n"
                f"Flow: {fmt_flow(r['flow'])} CFS\n"
                f"Upstream: {r['upstream']:g} ft\n"
                f"Downstream: {r['downstream']:g} ft\n\n"
                f"Source: MacVicar Consulting"
            )
        elif not above and previous:
            ntfy(
                f"{site} {cfg['name']} DROPPED BELOW THRESHOLD",
                f"{site} dropped below {fmt_flow(threshold)} CFS.\n\n"
                f"Flow: {fmt_flow(r['flow'])} CFS\n"
                f"Upstream: {r['upstream']:g} ft\n"
                f"Downstream: {r['downstream']:g} ft\n\n"
                f"Source: MacVicar Consulting",
                priority="default"
            )

    save_state(new_state)

if __name__ == "__main__":
    main()
