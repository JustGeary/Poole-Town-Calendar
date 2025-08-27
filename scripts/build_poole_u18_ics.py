# generate_calendar.py  (or your current script name)
import json, re, hashlib, os, sys
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request

API_URL = "https://faapi.jwhsolutions.co.uk/api/Fixtures/938310682?teamName=Poole%20Town%20FC%20Wessex%20U18%20Colts"
TEAM_NAME = "Poole Town FC Wessex U18 Colts"
OUTPUT = "poole_town_u18_colts_fixtures.ics"
STATE  = ".state_poole_u18.json"

def log(*a): print(*a, flush=True)
def crlf_join(lines): return "\r\n".join(lines) + "\r\n"
def esc(s): return (s or "").replace("\\","\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n","\\n")
def slug(s): return re.sub(r"[^a-z0-9]+","-", (s or "").lower()).strip("-")

def fetch():
    req = Request(API_URL, headers={"User-Agent":"PooleTownCalendar/1.0"})
    with urlopen(req) as r:
        data = json.load(r)
    if not isinstance(data, list):
        data = []
    log(f"[INFO] API returned {len(data)} items")
    if data:
        log("[INFO] First item sample:\n" + json.dumps(data[0], indent=2, ensure_ascii=False))
    return data

def parse_dt(item):
    v = item.get("fixtureDateTime")  # e.g. "07/09/25 14:00" (dd/MM/yy HH:mm) UK local
    if not v:
        raise ValueError("fixtureDateTime missing")
    for fmt in ("%d/%m/%y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            from zoneinfo import ZoneInfo
            dt_local = datetime.strptime(v, fmt)
            dt_utc = dt_local.replace(tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)
            return dt_utc, dt_utc + timedelta(hours=2)
        except Exception:
            continue
    raise ValueError(f"Could not parse fixtureDateTime '{v}'")

def load_state():
    if os.path.exists(STATE):
        with open(STATE, "r", encoding="utf-8") as f: return json.load(f)
    return {}

def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f: json.dump(s, f, indent=2, sort_keys=True)

def make_uid(start_utc, home, away):
    us_home = TEAM_NAME.lower() in (home or "").lower()
    opponent = (away if us_home else home) or "opponent"
    ts = start_utc.strftime("%Y%m%dT%H%M%SZ")
    hoa = "h" if us_home else "a"
    return f"ptfc-u18-{ts}-{hoa}-{slug(opponent)}@poole-town"

def build_event(item, state):
    home = (item.get("homeTeam") or "").strip()
    away = (item.get("awayTeam") or "").strip()
    venue = (item.get("location") or "").strip()
    comp  = (item.get("competition") or "").strip()

    start_utc, end_utc = parse_dt(item)
    uid = make_uid(start_utc, home, away)

    us_home = TEAM_NAME.lower() in home.lower()
    opponent = away if us_home else home
    summary = f"{TEAM_NAME} vs {opponent}" if us_home else f"{opponent} vs {TEAM_NAME}"

    desc_bits = [f"{home} vs {away}"]
    if comp:  desc_bits.append(f"Competition: {comp}")
    if venue: desc_bits.append(f"Venue: {venue}")
    description = "\\n".join(esc(x) for x in desc_bits)

    # bump SEQUENCE if item changed
    import hashlib
    fp = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
    seq = state.get(uid, {}).get("seq", 0)
    if state.get(uid, {}).get("fp") not in (None, fp):
        seq += 1
    state[uid] = {"seq": seq, "fp": fp}

    now = datetime.utcnow().replace(tzinfo=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return crlf_join([
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now}",
        f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"SEQUENCE:{seq}",
        f"SUMMARY:{esc(summary)}",
        f"LOCATION:{esc(venue)}",
        f"DESCRIPTION:{description}",
        "END:VEVENT",
    ]), state

def main():
    data = fetch()
    # Your endpoint already filters by teamName; don’t over-filter.
    # Sort by kickoff
    def safe_key(it):
        try: return parse_dt(it)[0]
        except: return datetime.max.replace(tzinfo=timezone.utc)
    data.sort(key=safe_key)

    state = load_state()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//PooleTown//U18 Fixtures via FullTimeAPI//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    built = 0
    for it in data:
        try:
            ev, state = build_event(it, state)
            lines.append(ev.strip())
            built += 1
        except Exception as e:
            log(f"[WARN] Skipping item: {e}")
    lines.append("END:VCALENDAR")

    with open(OUTPUT, "w", newline="") as f:
        f.write(crlf_join(lines))
    save_state(state)
    log(f"[INFO] Wrote {OUTPUT} with {built} events.")

if __name__ == "__main__":
    main()
