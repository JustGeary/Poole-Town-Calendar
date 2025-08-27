# scripts/build_poole_u18_ics.py
# Builds poole_town_u18_colts_fixtures.ics from FullTimeAPI
import json, re, hashlib, os, sys
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request

API_URL = "https://faapi.jwhsolutions.co.uk/api/Fixtures/938310682?teamName=Poole%20Town%20FC%20Wessex%20U18%20Colts"
TEAM_NAME = "Poole Town FC Wessex U18 Colts"   # must match your team identity
OUTPUT = "poole_town_u18_colts_fixtures.ics"
STATE  = ".state_poole_u18.json"               # stores per-UID SEQUENCE + fingerprint

def crlf_join(lines): return "\r\n".join(lines) + "\r\n"
def slug(s): return re.sub(r"[^a-z0-9]+","-", (s or "").lower()).strip("-")

def fetch():
    req = Request(API_URL, headers={"User-Agent":"PooleTownCalendar/1.0"})
    with urlopen(req) as r:
        return json.load(r)

def load_state():
    if os.path.exists(STATE):
        with open(STATE, "r", encoding="utf-8") as f: return json.load(f)
    return {}

def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f: json.dump(s, f, indent=2, sort_keys=True)

def parse_dt(item):
    """
    Try multiple shapes: ISO datetime, date+time strings, etc., assume UK local if naive.
    Returns (start_utc, end_utc)
    """
    # 1) ISO datetime candidates
    for k in ("kickoffDateTime","kickOffDateTime","koDateTime","dateTime","kickoff"):
        v = item.get(k)
        if v:
            try:
                # Accept '2025-09-10T19:45:00Z' or without Z
                if v.endswith("Z"):
                    dt = datetime.fromisoformat(v.replace("Z","+00:00"))
                else:
                    dt = datetime.fromisoformat(v)
                if dt.tzinfo is None:
                    # Treat naive as Europe/London then convert to UTC
                    from zoneinfo import ZoneInfo
                    dt = dt.replace(tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return dt, dt + timedelta(hours=2)
            except Exception:
                pass

    # 2) date + time fields
    date_keys = ("kickoffDate","date","matchDate")
    time_keys = ("kickoffTime","time","matchTime")
    date, time_ = None, None
    for dk in date_keys:
        if item.get(dk): date = item[dk]; break
    for tk in time_keys:
        if item.get(tk): time_ = item[tk]; break
    if date and time_:
        # Normalize time like "7:45 PM" or "19:45"
        try:
            try:
                dt_local = datetime.strptime(f"{date} {time_}", "%Y-%m-%d %H:%M")
            except ValueError:
                dt_local = datetime.strptime(f"{date} {time_}", "%Y-%m-%d %I:%M %p")
            from zoneinfo import ZoneInfo
            dt_utc = dt_local.replace(tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)
            return dt_utc, dt_utc + timedelta(hours=2)
        except Exception:
            pass

    raise ValueError("Could not parse kickoff datetime from item")

def get_text(item, *keys):
    for k in keys:
        v = item.get(k)
        if v: return str(v).strip()
    return ""

def escape_ics(s):
    return (s or "").replace("\\","\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n","\\n")

def make_uid(start_utc, home_name, away_name):
    # Decide H/A relative to TEAM_NAME (string contains match)
    us_home = TEAM_NAME.lower() in (home_name or "").lower()
    opponent = (away_name if us_home else home_name) or "opponent"
    ts = start_utc.strftime("%Y%m%dT%H%M%SZ")
    hoa = "h" if us_home else "a"
    return f"ptfc-u18-{ts}-{hoa}-{slug(opponent)}@poole-town"

def build_event(item, state):
    # Map fields defensively
    home = get_text(item, "homeTeam","home","home_name","homeClub")
    away = get_text(item, "awayTeam","away","away_name","awayClub")
    venue = get_text(item, "venue","ground","location")
    status = get_text(item, "status","matchStatus").upper()

    start_utc, end_utc = parse_dt(item)
    uid = make_uid(start_utc, home, away)

    # Prepare SUMMARY
    us_home = TEAM_NAME.lower() in home.lower()
    opponent = away if us_home else home
    summary = f"{TEAM_NAME} vs {opponent}" if us_home else f"{opponent} vs {TEAM_NAME}"

    # Prepare DESCRIPTION
    bits = [f"{home} vs {away}"]
    if status: bits.append(f"Status: {status}")
    if venue:  bits.append(f"Venue: {venue}")
    description = "\\n".join(escape_ics(x) for x in bits)

    # Bump SEQUENCE if item changed
    fingerprint = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
    seq = state.get(uid, {}).get("seq", 0)
    last = state.get(uid, {}).get("fp")
    if last and last != fingerprint:
        seq += 1
    state[uid] = {"seq": seq, "fp": fingerprint}

    now = datetime.utcnow().replace(tzinfo=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now}",
        f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"SEQUENCE:{seq}",
        f"SUMMARY:{escape_ics(summary)}",
        f"LOCATION:{escape_ics(venue)}",
        f"DESCRIPTION:{description}",
        # Optional alarm:
        # "BEGIN:VALARM",
        # "TRIGGER:-PT60M",
        # "ACTION:DISPLAY",
        # f"DESCRIPTION:{escape_ics(summary)}",
        # "END:VALARM",
        "END:VEVENT",
    ]
    return crlf_join(lines), state, start_utc

def main():
    data = fetch()
    # Some endpoints return a list; some wrap in an object. Normalize to a list.
    if isinstance(data, dict):
        # try common containers
        for k in ("fixtures","data","items","matches"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
        else:
            data = [data]
    elif not isinstance(data, list):
        print("Unexpected payload; writing empty ICS.", file=sys.stderr)
        data = []

    # Filter to our team if needed (endpoint already filters by teamName, but be safe)
    items = []
    for it in data:
        h, a = get_text(it, "homeTeam","home"), get_text(it, "awayTeam","away")
        if TEAM_NAME.lower() in (h.lower() + " " + a.lower()):
            items.append(it)

    # Sort by kickoff
    def sort_key(it):
        try:
            return parse_dt(it)[0]
        except Exception:
            return datetime.max.replace(tzinfo=timezone.utc)
    items.sort(key=sort_key)

    state = load_state()

    body = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//PooleTown//U18 Fixtures via FullTimeAPI//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for it in items:
        try:
            ev, state, _ = build_event(it, state)
            body.append(ev.strip())
        except Exception as e:
            print(f"Skip item due to parse error: {e}", file=sys.stderr)
            continue

    body.append("END:VCALENDAR")
    with open(OUTPUT, "w", newline="") as f:
        f.write(crlf_join(body))

    save_state(state)
    print(f"Wrote {OUTPUT} with {len(items)} events.")

if __name__ == "__main__":
    main()
