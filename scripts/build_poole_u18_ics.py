# generate_calendar.py
# Build poole_town_u18_colts_fixtures.ics from FullTimeAPI (fixtures + results)
# Times are written in UTC (...Z). Calendar apps render in local time (e.g., Europe/London),
# so BST/GMT changes are handled automatically by the client.

import json
import os
import re
import sys
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

# --- CONFIG -------------------------------------------------------------------
FIXTURES_URL = "https://faapi.jwhsolutions.co.uk/api/Fixtures/938310682?teamName=Poole%20Town%20FC%20Wessex%20U18%20Colts"
RESULTS_URL  = "https://faapi.jwhsolutions.co.uk/api/Results/938310682?teamName=Poole%20Town%20FC%20Wessex%20U18%20Colts"

TEAM_NAME    = "Poole Town FC Wessex U18 Colts"
OUTPUT       = "poole_town_u18_colts_fixtures.ics"
STATE        = ".state_poole_u18.json"  # stores per-UID seq + fingerprint

# Handy links (TinyURL versions)
LINKS = [
    "PTYFC Results/Fixtures: https://tinyurl.com/3rcea6d6",
    "League Table: https://tinyurl.com/2p3zzska",
    "League Fixtures: https://tinyurl.com/bdhdmzcn",
    "League Results: https://tinyurl.com/bs6ppntx",
]

# Event duration: keep fixed at 2 hours (KO + HT + buffer)
EVENT_DURATION = timedelta(hours=2)

# --- UTILITIES ----------------------------------------------------------------
def log(*a): print(*a, flush=True)

def crlf_join(lines):  # ICS requires CRLF line endings
    return "\r\n".join(lines) + "\r\n"

def esc(s: str) -> str:
    """Escape ICS special chars and newlines in text fields."""
    return (s or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")

def clean_team(s: str) -> str:
    """Light normalisation to reduce matching issues."""
    s = (s or "").lower()
    s = re.sub(r"\b(fc|afc)\b", "", s)
    s = s.replace("u18s", "u18")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def fetch_json(url: str):
    req = Request(url, headers={"User-Agent": "PooleTownCalendar/1.0"})
    with urlopen(req) as r:
        data = json.load(r)
    if isinstance(data, list):
        return data
    return []

# --- DATE/TIME ----------------------------------------------------------------
def parse_fixture_dt_local_to_utc(local_dt_str: str) -> datetime:
    """
    Convert FullTime 'fixtureDateTime' like '07/09/25 14:00' (dd/MM/yy HH:mm) or 'dd/MM/YYYY HH:mm'
    from Europe/London to UTC.
    """
    if not local_dt_str:
        raise ValueError("fixtureDateTime missing")
    for fmt in ("%d/%m/%y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            from zoneinfo import ZoneInfo
            dt_local = datetime.strptime(local_dt_str, fmt)
            return dt_local.replace(tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)
        except Exception:
            continue
    raise ValueError(f"Could not parse fixtureDateTime '{local_dt_str}'")

def key_from_date_and_teams(date_str: str, home: str, away: str) -> str:
    """
    Create a canonical key for fixture<->result matching using local date (no TZ)
    and team names. Accepts 'dd/MM/yy[ HH:MM]' or 'dd/MM/YYYY[ HH:MM]'.
    """
    patterns = ("%d/%m/%y %H:%M", "%d/%m/%Y %H:%M", "%d/%m/%y", "%d/%m/%Y")
    for fmt in patterns:
        try:
            d = datetime.strptime(date_str, fmt)
            return f"{d.strftime('%Y%m%d')}|{clean_team(home)}|{clean_team(away)}"
        except Exception:
            pass
    # As a last resort, return raw-ish key
    return f"{date_str}|{clean_team(home)}|{clean_team(away)}"

# --- STATE --------------------------------------------------------------------
def load_state():
    if os.path.exists(STATE):
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, sort_keys=True)

# --- UID & SEQUENCE -----------------------------------------------------------
def make_uid(start_utc: datetime, home: str, away: str) -> str:
    us_home = TEAM_NAME.lower() in (home or "").lower()
    opponent = (away if us_home else home) or "opponent"
    ts = start_utc.strftime("%Y%m%dT%H%M%SZ")
    hoa = "h" if us_home else "a"
    return f"ptfc-u18-{ts}-{hoa}-{slug(opponent)}@poole-town"

# --- MAIN BUILD ---------------------------------------------------------------
def main():
    fixtures = fetch_json(FIXTURES_URL)
    results  = fetch_json(RESULTS_URL)

    log(f"[INFO] Fixtures: {len(fixtures)}")
    if fixtures:
        log("[INFO] First fixture sample:\n" + json.dumps(fixtures[0], indent=2, ensure_ascii=False))
    log(f"[INFO] Results: {len(results)}")
    if results:
        log("[INFO] First result sample:\n" + json.dumps(results[0], indent=2, ensure_ascii=False))

    # Build result lookup
    res_map = {}
    scored_count = 0
    for r in results:
        # Results payload fields (defensive): date might be 'resultDateTime' or reuse 'fixtureDateTime' or 'date'
        date_str = r.get("resultDateTime") or r.get("fixtureDateTime") or r.get("date") or ""
        home     = r.get("homeTeam") or r.get("home") or ""
        away     = r.get("awayTeam") or r.get("away") or ""
        hs       = r.get("homeScore") or r.get("homeGoals") or r.get("home_score") or r.get("homeResult")
        as_      = r.get("awayScore") or r.get("awayGoals") or r.get("away_score") or r.get("awayResult")
        comp     = r.get("competition") or ""
        venue    = r.get("location") or r.get("ground") or ""

        key = key_from_date_and_teams(date_str, home, away)
        res_map[key] = {"hs": hs, "as": as_, "competition": comp, "location": venue}
        if hs is not None and as_ is not None:
            scored_count += 1
    log(f"[INFO] Result entries with scores: {scored_count}")

    # Sort fixtures by kickoff UTC
    def safe_key(fx):
        try:
            return parse_fixture_dt_local_to_utc(fx.get("fixtureDateTime") or fx.get("date") or "")
        except Exception:
            return datetime.max.replace(tzinfo=timezone.utc)

    fixtures.sort(key=safe_key)
    state = load_state()

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//PooleTown//U18 Fixtures via FullTimeAPI//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    built = 0
    for fx in fixtures:
        try:
            # Fixture fields (as per your sample)
            f_date_local = fx.get("fixtureDateTime") or fx.get("date") or ""
            home = (fx.get("homeTeam") or "").strip()
            away = (fx.get("awayTeam") or "").strip()
            venue = (fx.get("location") or fx.get("ground") or "").strip()
            comp  = (fx.get("competition") or "").strip()

            start_utc = parse_fixture_dt_local_to_utc(f_date_local)
            end_utc   = start_utc + EVENT_DURATION
            uid       = make_uid(start_utc, home, away)
            us_home   = TEAM_NAME.lower() in home.lower()
            opponent  = away if us_home else home

            # Find matching result (same local date string & teams)
            rkey = key_from_date_and_teams(f_date_local, home, away)
            res  = res_map.get(rkey)

            # SUMMARY (inject score if present)
            if res and (res.get("hs") is not None and res.get("as") is not None):
                hs = str(res["hs"]).strip()
                as_ = str(res["as"]).strip()
                if us_home:
                    summary = f"{TEAM_NAME} {hs}–{as_} {opponent}"
                else:
                    summary = f"{opponent} {hs}–{as_} {TEAM_NAME}"
            else:
                summary = f"{TEAM_NAME} vs {opponent}" if us_home else f"{opponent} vs {TEAM_NAME}"

            # DESCRIPTION
            desc_bits = [f"{home} vs {away}"]
            if comp:  desc_bits.append(f"Competition: {comp}")
            if venue: desc_bits.append(f"Venue: {venue}")
            if res and (res.get("hs") is not None and res.get("as") is not None):
                desc_bits.append(f"Result: {home} {res['hs']}–{res['as']} {away}")
            # Always append your helpful links
            desc_bits.extend(LINKS)
            description = "\\n".join(esc(x) for x in desc_bits)

            # SEQUENCE bump if fixture+result composite changed
            fingerprint = hashlib.sha256(
                json.dumps({"fx": fx, "res": res}, sort_keys=True, default=str).encode()
            ).hexdigest()
            seq = state.get(uid, {}).get("seq", 0)
            last = state.get(uid, {}).get("fp")
            if last and last != fingerprint:
                seq += 1
            state[uid] = {"seq": seq, "fp": fingerprint}

            now = datetime.utcnow().replace(tzinfo=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            lines.extend([
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
            ])
            built += 1

        except Exception as e:
            log(f"[WARN] Skipping fixture due to parse/match error: {e}")

    lines.append("END:VCALENDAR")

    with open(OUTPUT, "w", newline="") as f:
        f.write(crlf_join(lines))
    save_state(state)

    log(f"[INFO] Wrote {OUTPUT} with {built} events.")
    if built == 0:
        log("[WARN] 0 events written — check payload keys and URLs.")

if __name__ == "__main__":
    main()
