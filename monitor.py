import os
import re
import json
import html
import urllib.request
from pathlib import Path

SOURCE_URL = "https://www.macvicarconsulting.com/readings/readingsmobil.htm"
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
STATE_FILE = Path("state.json")

# Notify whenever flow changes by this amount or more.
CHANGE_THRESHOLD = 20

SPILLWAYS = {
    "S99":  {"name": "Fort Pierce"},
    "S49":  {"name": "Port St. Lucie"},
    "S97":  {"name": "Palm City"},
    "S46":  {"name": "Jupiter"},
    "S44":  {"name": "N. Palm Beach"},
    "S155": {"name": "Lake Worth"},
    "S41":  {"name": "Boynton Beach"},
    "S40":  {"name": "Delray Beach"},
    "S37A": {"name": "Pompano Beach"},
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

            m = re.search(
                rf"\b{re.escape(site)}\b(.*)",
                text,
                flags=re.I
            )

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
            "Title": title.encode(
                "ascii",
                errors="ignore"
            ).decode(),
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

    for site, cfg in SPILLWAYS.items():

        if site not in readings:
            continue

        r = readings[site]
        current_flow = r["flow"]

        previous_flow = old_state.get(site, {}).get("flow")

        print(
            f"{site} {cfg['name']}: "
            f"flow={current_flow} "
            f"up={r['upstream']} "
            f"down={r['downstream']} "
            f"previous={previous_flow}"
        )

        # First observation establishes the baseline.
        if previous_flow is not None:

            previous_flow = float(previous_flow)
            change = current_flow - previous_flow

            if abs(change) >= CHANGE_THRESHOLD:

                direction = "INCREASED" if change > 0 else "DECREASED"
                sign = "+" if change > 0 else ""

                ntfy(
                    f"{site} {cfg['name']} {direction} {abs(change):g} CFS",
                    f"{site} {cfg['name']}\n\n"
                    f"Previous: {fmt_flow(previous_flow)} CFS\n"
                    f"Current: {fmt_flow(current_flow)} CFS\n"
                    f"Change: {sign}{change:g} CFS\n\n"
                    f"Upstream: {r['upstream']:g} ft\n"
                    f"Downstream: {r['downstream']:g} ft\n\n"
                    f"Source: MacVicar Consulting"
                )

        # Always save the newest observed flow as the next baseline.
        new_state[site] = {
            "flow": current_flow
        }

    save_state(new_state)


if __name__ == "__main__":
    main()
