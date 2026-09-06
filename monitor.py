import os
import re
import json
import html
import urllib.request
from datetime import datetime
from email.header import Header
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

SOURCE_URL = "https://www.macvicarconsulting.com/readings/readingsmobil.htm"
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
STATE_FILE = Path("state.json")
HISTORY_FILE = Path("spillway_history.json")
LOCAL_TZ = ZoneInfo("America/New_York")

# Existing flow-change alert behavior.
CHANGE_THRESHOLD = 20

# Predictive warning settings.
PREDICTION_DISTANCE_FT = 0.10
MIN_HISTORY_EVENTS = 3

SPILLWAYS = {
    "S80":  {"name": "Stuart Locks"},
    "S99":  {"name": "Fort Pierce"},
    "S49":  {"name": "Port St. Lucie"},
    "S97":  {"name": "Palm City"},
    "S46":  {"name": "Jupiter"},
    "S44":  {"name": "N. Palm Beach"},
    "S155": {"name": "Lake Worth"},
    "S41":  {"name": "Boynton Beach"},
    "S40":  {"name": "Delray Beach"},
    "G56":  {"name": "Boca Raton"},
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


def load_json(path, default):
    if not path.exists():
        return default

    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def ntfy(title, message, priority="high"):
    url = f"https://ntfy.sh/{NTFY_TOPIC}"

    # Use custom emojis for open/close events; keep the default pair elsewhere.
    if title.endswith(" OPENED"):
        prefix = "🥳"
    elif title.endswith(" CLOSED"):
        prefix = "🥀🙂‍↕️"
    else:
        prefix = "🫪🚨"

    # S46 Jupiter increases/openings are especially important: make them
    # ntfy max-priority alerts and clearly mark them as SUPER ALERTS.
    if title.startswith("S46 Jupiter OPENED") or title.startswith("S46 Jupiter INCREASED"):
        title = f"SUPER ALERT — {title}"
        prefix = "🚨🚨🚨"
        priority = "max"

    # RFC 2047 keeps the HTTP header ASCII-safe while preserving Unicode.
    encoded_title = Header(f"{prefix} {title}", "utf-8").encode()

    req = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
        method="POST",
        headers={
            "Title": encoded_title,
            "Priority": priority,
            "Content-Type": "text/plain; charset=utf-8",
        },
    )

    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def fmt_flow(x):
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def event_levels(history, site, event_type):
    levels = []
    for event in history.get("events", []):
        if event.get("site") != site or event.get("event") != event_type:
            continue
        try:
            levels.append(float(event["upstream"]))
        except (KeyError, TypeError, ValueError):
            continue
    return levels


def record_transition(history, site, name, event_type, current):
    event = {
        "site": site,
        "name": name,
        "event": event_type,
        "timestamp": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
        "flow": current["flow"],
        "upstream": current["upstream"],
        "downstream": current["downstream"],
    }
    history.setdefault("events", []).append(event)
    return event


def transition_message(site, name, event_type, event, history):
    levels = event_levels(history, site, event_type)
    avg_level = mean(levels)
    label = "opening" if event_type == "opened" else "closing"
    state_change = "CLOSED → OPEN" if event_type == "opened" else "OPEN → CLOSED"

    extra = ""
    if site == "S49" and event_type == "closed":
        extra = "\n\nAmir, commence the SHADATHON."

    return (
        f"{site} {name}\n\n"
        f"State: {state_change}\n"
        f"Current flow: {fmt_flow(event['flow'])} CFS\n"
        f"Upstream at {label}: {event['upstream']:.2f} ft\n"
        f"Downstream: {event['downstream']:.2f} ft\n\n"
        f"Historical {label}s: {len(levels)}\n"
        f"Average {label} level: {avg_level:.2f} ft"
        f"{extra}\n\n"
        f"Source: MacVicar Consulting"
    )


def prediction_message(site, name, action, current, threshold, count):
    event_word = "opening" if action == "OPEN" else "closing"
    distance = abs(current["upstream"] - threshold)

    return (
        f"{site} {name}\n\n"
        f"Current upstream: {current['upstream']:.2f} ft\n"
        f"Historical {event_word} level: {threshold:.2f} ft\n"
        f"Distance: {distance:.2f} ft\n"
        f"Current flow: {fmt_flow(current['flow'])} CFS\n"
        f"Historical {event_word}s: {count}\n\n"
        f"Source: MacVicar Consulting"
    )


def main():
    page = fetch_html()
    readings = parse_readings(page)

    missing = [s for s in SPILLWAYS if s not in readings]
    if missing:
        print("WARNING: Could not parse:", ", ".join(missing))

    old_state = load_json(STATE_FILE, {})
    new_state = {site: dict(value) for site, value in old_state.items()}
    history = load_json(HISTORY_FILE, {"events": []})

    if not isinstance(history, dict) or not isinstance(history.get("events"), list):
        history = {"events": []}

    for site, cfg in SPILLWAYS.items():
        if site not in readings:
            continue

        r = readings[site]
        current_flow = r["flow"]
        current_open = current_flow > 0

        site_state = dict(old_state.get(site, {}))
        baseline = site_state.get("baseline")

        # If the earlier implementation already seeded last_flow, use it once
        # to migrate cleanly to the lighter open/closed state model.
        previous_open = site_state.get("is_open")
        if previous_open is None and site_state.get("last_flow") is not None:
            previous_open = float(site_state["last_flow"]) > 0

        # Remove old per-reading fields so state.json does not change every run.
        site_state.pop("last_flow", None)
        site_state.pop("last_upstream", None)
        site_state.pop("last_downstream", None)

        if baseline is None:
            baseline = current_flow
            site_state["baseline"] = current_flow

        baseline = float(baseline)
        change = current_flow - baseline

        print(
            f"{site} {cfg['name']}: "
            f"flow={current_flow} baseline={baseline} change={change:+g}"
        )

        # First observation after the upgrade: establish status without
        # manufacturing a false opening or closing event.
        if previous_open is None:
            site_state["is_open"] = current_open
            site_state.setdefault("open_warning_sent", False)
            site_state.setdefault("close_warning_sent", False)
            new_state[site] = site_state
            continue

        previous_open = bool(previous_open)
        transition = None

        if not previous_open and current_open:
            transition = "opened"
        elif previous_open and not current_open:
            transition = "closed"

        if transition:
            event = record_transition(
                history,
                site,
                cfg["name"],
                transition,
                r,
            )

            if transition == "opened":
                ntfy(
                    f"{site} {cfg['name']} OPENED",
                    transition_message(site, cfg["name"], "opened", event, history),
                )
            else:
                ntfy(
                    f"{site} {cfg['name']} CLOSED",
                    transition_message(site, cfg["name"], "closed", event, history),
                )

            # The transition is already the meaningful alert; reset the CFS
            # baseline so it does not also produce a duplicate 20-CFS alert.
            site_state["baseline"] = current_flow
            site_state["open_warning_sent"] = False
            site_state["close_warning_sent"] = False

        else:
            # Keep the existing 20+ CFS change alerts.
            if abs(change) >= CHANGE_THRESHOLD:
                direction = "INCREASED" if change > 0 else "DECREASED"
                sign = "+" if change > 0 else ""

                ntfy(
                    f"{site} {cfg['name']} {direction} {abs(change):g} CFS",
                    f"{site} {cfg['name']}\n\n"
                    f"Previous baseline: {fmt_flow(baseline)} CFS\n"
                    f"Current flow: {fmt_flow(current_flow)} CFS\n"
                    f"Change: {sign}{change:g} CFS\n\n"
                    f"Upstream: {r['upstream']:g} ft\n"
                    f"Downstream: {r['downstream']:g} ft\n\n"
                    f"Source: MacVicar Consulting"
                )
                site_state["baseline"] = current_flow

            # Closed spillway: warn within 0.10 ft below the historical opening
            # level after at least 3 recorded openings.
            open_levels = event_levels(history, site, "opened")
            if not current_open and len(open_levels) >= MIN_HISTORY_EVENTS:
                open_threshold = mean(open_levels)
                in_open_zone = (
                    open_threshold - PREDICTION_DISTANCE_FT
                    <= r["upstream"]
                    <= open_threshold
                )

                if in_open_zone:
                    if not site_state.get("open_warning_sent", False):
                        ntfy(
                            f"{site} {cfg['name']} MAY OPEN SOON",
                            prediction_message(
                                site,
                                cfg["name"],
                                "OPEN",
                                r,
                                open_threshold,
                                len(open_levels),
                            ),
                        )
                        site_state["open_warning_sent"] = True
                else:
                    site_state["open_warning_sent"] = False
            else:
                site_state["open_warning_sent"] = False

            # Open spillway: warn within 0.10 ft above the historical closing
            # level after at least 3 recorded closings.
            close_levels = event_levels(history, site, "closed")
            if current_open and len(close_levels) >= MIN_HISTORY_EVENTS:
                close_threshold = mean(close_levels)
                in_close_zone = (
                    close_threshold
                    <= r["upstream"]
                    <= close_threshold + PREDICTION_DISTANCE_FT
                )

                if in_close_zone:
                    if not site_state.get("close_warning_sent", False):
                        ntfy(
                            f"{site} {cfg['name']} MAY CLOSE SOON",
                            prediction_message(
                                site,
                                cfg["name"],
                                "CLOSE",
                                r,
                                close_threshold,
                                len(close_levels),
                            ),
                        )
                        site_state["close_warning_sent"] = True
                else:
                    site_state["close_warning_sent"] = False
            else:
                site_state["close_warning_sent"] = False

        site_state["is_open"] = current_open
        new_state[site] = site_state

    save_json(STATE_FILE, new_state)
    save_json(HISTORY_FILE, history)


if __name__ == "__main__":
    main()
