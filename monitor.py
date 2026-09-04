import os
import re
import json
import html
import urllib.request
from pathlib import Path

SOURCE_URL = "https://www.macvicarconsulting.com/readings/readingsmobil.htm"
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
STATE_FILE = Path("state.json")

# Alert fires when flow crosses this threshold.
SPILLWAYS = {
    "S99":  {"name": "Fort Pierce",          "threshold": 1},
    "S49":  {"name": "Port St. Lucie",          "threshold": 1},
    "S97":  {"name": "Palm City",          "threshold": 1},
    "S46":  {"name": "Jupiter",          "threshold": 1},
    "S44":  {"name": "N. Palm Beach",   "threshold": 1},
    "S155": {"name": "Lake Worth",     "threshold": 1},
    "S41":  {"name": "Boynton Beach",   "threshold": 1},
    "S40":  {"name": "Delray Beach",    "threshold": 1},
    "S37A": {"name": "Pompano Beach",   "threshold": 1},
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
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S)

    for row in rows:
        text = strip_tags(row)

        for site in SPILLWAYS:
            if not re.search(rf"\b{re.escape(site)}\b", text, flags=re.I):
                continue

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
    STATE_FILE.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n"
    )


def ntfy(title, message, priority="high"):
    url = f"https://ntfy.sh/{NTFY_TOPIC}"

    req = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
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
    new_state = {}

    for site, cfg in SPILLWAYS.items():
        if site not in readings:
            # Preserve existing state if this structure could not be parsed.
            if site in old_state:
                new_state[site] = old_state[site]
            continue

        r = readings[site]
        threshold = float(cfg["threshold"])
        above = r["flow"] >= threshold

        previous = old_state.get(site, {}).get("above")

        # Only persist threshold state.
        new_state[site] = {
            "above": above
        }

        print(
            f"{site} {cfg['name']}: "
            f"flow={r['flow']} "
            f"up={r['upstream']} "
            f"down={r['downstream']} "
            f"threshold={threshold} "
            f"above={above}"
        )

        # No alert if we don't have a previous state yet.
        if previous is None:
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
    ntfy(
        "SPILLWAY ALERT TEST",
        "Success! GitHub Actions is connected to ntfy and alerts are working.",
        priority="high"
    )
    main()
