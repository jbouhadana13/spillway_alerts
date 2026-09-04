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

# Alert when flow moves this much from the last alert/baseline.
CHANGE_THRESHOLD = 20

# Predictive warning settings.
PREDICTION_DISTANCE_FT = 0.10
MIN_HISTORY_EVENTS = 3

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

    # Prefix the exact requested emojis in the title. RFC 2047 encoding keeps
    # the HTTP header ASCII-safe while preserving the Unicode title in ntfy.
    encoded_title = Header(f"🫪🚨 {title}", "utf-8").encode()

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


def record_transition(history, site, name, event_type, previous, current):
    event = {
        "site": site,
        "name": name,
        "event": event_type,
        "timestamp": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
        "previous_flow": previous["flow"],
        "current_flow": current["flow"],
        "previous_upstream": previous["upstream"],
        "upstream": current["upstream"],
        "downstream": current["downstream"],
    }
    history.setdefault("events", []).append(event)
    return event


def transition_message(site, name, event_type, event, history):
    levels = event_levels(history, site, event_type)
    avg_level = mean(levels)
    label = "opening" if event_type == "opened" else "closing"

    return (
        f"{site} {name}\n\n"
        f"Previous flow: {fmt_flow(event['previous_flow'])} CFS\n"
        f"Current flow: {fmt_flow(event['current_flow'])} CFS\n"
        f"Upstream at {label}: {event['upstream']:.2f} ft\n"
        f"Previous upstream: {event['previous_upstream']:.2f} ft\n"
        f"Downstream: {event['downstream']:.2f} ft\n\n"
        f"Historical {label}s: {len(levels)}\n"
        f"Average {label} level: {avg_level:.2f} ft\n\n"
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
        previous_flow = site_state.get("last_flow")
        previous_upstream = site_state.get("last_upstream")
        previous_downstream = site_state.get("last_downstream")

        # Preserve the existing flow-change baseline behavior.
        if baseline is None:
            baseline = current_flow
            site_state["baseline"] = current_flow

        baseline = float(baseline)
        change = current_flow - baseline

        print(
            f"{site} {cfg['name']}: "
            f"flow={current_flow} baseline={baseline} change={change:+g}"
        )

        # Migration / first observation: establish the previous-reading fields
        # without recording a false opening or closing event.
        if previous_flow is None or previous_upstream is None:
            site_state["last_flow"] = current_flow
            site_state["last_upstream"] = r["upstream"]
            site_state["last_downstream"] = r["downstream"]
            site_state.setdefault("open_warning_sent", False)
            site_state.setdefault("close_warning_sent", False)
            new_state[site] = site_state
            continue

        previous = {
            "flow": float(previous_flow),
            "upstream": float(previous_upstream),
            "downstream": float(previous_downstream) if previous_downstream is not None else r["downstream"],
        }
        previous_open = previous["flow"] > 0
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
                previous,
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

            # A transition is already a meaningful alert, so reset the flow
            # baseline here to avoid a duplicate 20-CFS notification.
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

            # Predict an opening only after at least 3 recorded openings.
            open_levels = event_levels(history, site, "opened")
            if not current_open and len(open_levels) >= MIN_HISTORY_EVENTS:
                open_threshold = mean(open_levels)
                in_open_zone = abs(r["upstream"] - open_threshold) <= PREDICTION_DISTANCE_FT
                approaching_open = r["upstream"] >= previous["upstream"]

                if in_open_zone and approaching_open:
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
                elif not in_open_zone:
                    site_state["open_warning_sent"] = False
            else:
                site_state["open_warning_sent"] = False

            # Predict a closing only after at least 3 recorded closings.
            close_levels = event_levels(history, site, "closed")
            if current_open and len(close_levels) >= MIN_HISTORY_EVENTS:
                close_threshold = mean(close_levels)
                in_close_zone = abs(r["upstream"] - close_threshold) <= PREDICTION_DISTANCE_FT
                approaching_close = r["upstream"] <= previous["upstream"]

                if in_close_zone and approaching_close:
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
                elif not in_close_zone:
                    site_state["close_warning_sent"] = False
            else:
                site_state["close_warning_sent"] = False

        # Persist the latest observation for the next 10-minute comparison.
        site_state["last_flow"] = current_flow
        site_state["last_upstream"] = r["upstream"]
        site_state["last_downstream"] = r["downstream"]
        new_state[site] = site_state

    save_json(STATE_FILE, new_state)
    save_json(HISTORY_FILE, history)


if __name__ == "__main__":
    main()
