# scripts/build_poole_u18_ics.py
# Build poole_town_u18_colts_fixtures.ics from FullTimeAPI (fixtures + results)
# - Uses UTC in ICS (...Z); clients display local time automatically (BST/GMT handled)
# - Merges results into SUMMARY/DESCRIPTION when available
# - Writes CRLF line endings in binary (Outlook/Google/Apple friendly)
# - Skips writing if the API returns 0 fixtures or if 0 events are built (keeps last good file)
# - Tracks changes and writes notify.txt (optional Telegram step in workflow can send it)

import json
import os
import re
import sys
import time
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# --- CONFIG -------------------------------------------------------------------
FIXTURES_URL = "https://faapi.jwhsolutions.co.uk/api/Fixtures/938310682?teamName=Poole%20Town%20FC%20Wessex%20U18%20Colts"
RESULTS_URL  = "https://faapi.jwhsolutions.co.uk/api/Results/938310682?teamName=Poole%20Town%20FC%20Wessex%20U18%20Colts"

TEAM_NAME = "Poole Town FC Wessex U18 Colts"
OUTPUT    = "poole_town_u18_colts_fixtures.ics"
STATE     = ".state_poole_u18.json"  # stores per-UID seq + fingerprint + snapshot
NOTIFY_FILE = "notify.txt"           # human-readable change log (optional Telegram)

# Handy links (TinyURL versions, shortened labels)
LINKS = [
    "PTYFC Res/Fix: https://tinyurl.com/3rcea6d6",
    "League Table: https://tinyurl.com/2p3zzska",
    "League Fixtures: https://tinyurl.com/bdhdmzcn",
    "League Results: https://tinyurl.com/bs6ppntx",
]

# Fixed event duration (KO + HT + buffer)
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

def fmt_local(dt_utc: datetime) -> str:
    """Pretty Europe/London time for notifications."""
    return dt_utc.astimezone(ZoneInfo("Europe/London")).strftime("%a %d %b %Y %H:%M")

def tg_escape(s: str) -> str:
    """Minimal HTML escaping for Telegram (if you enable the step)."""
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def fetch_json(url: str, timeout=15, retries=3, backoff=2.0):
    """GET JSON with small retry/backoff; robust to list-or-dict shapes."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": "PooleTownCalendar/1.0 (+github.com/JustGeary/Poole-Town-Calendar)",
                    "Accept": "application/json",
                },
            )
            with urlopen(req, timeout=timeout) as r:
                raw = r.read()
                text = raw.decode("utf-8", "ignore")
                status = getattr(r, "status", 200)
                log(f"[DEBUG] GET {url} -> {status} ({len(text)} bytes)")
        except (HTTPError, URLError) as e:
            last_err = e
            log(f"[WARN] attempt {attempt}/{retries} failed: {e}")
            time.sleep(backoff ** (attempt - 1))
            continue

        try:
            data = json.loads(text)
        except Exception as e:
            log(f"[WARN] JSON decode failed: {e}. First 200 chars:\n{text[:200]}")
            return []

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in ("fixtures", "results", "data", "items", "matches"):
                if isinstance(data.get(k), list):
                    log(f"[DEBUG] Using list at key '{k}' ({len(data[k])} items)")
                    return data[k]
            for k, v in data.items():
                if isinstance(v, list):
                    log(f"[DEBUG] Using first list at key '{k}' ({len(v)} items)")
                    return v
            log(f"[WARN] JSON object had no list values. Keys: {list(data.keys())}")
            return []
        log(f"[WARN] Unexpected JSON type: {type(data)}")
        return []
    log(f"[ERROR] All attempts failed: {last_err}")
    return []

# --- DATE/TIME ----------------------------------------------------------------
def parse_fixture_dt_local_to_utc(local_dt_str: str) -> datetime:
    """Convert FullTime 'fixtureDateTime' like '07/09/25 14:00' or 'dd/MM/YYYY HH:mm' to UTC."""
    if not local_dt_str:
        raise ValueError("fixtureDateTime missing")
    for fmt in ("%d/%m/%y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            dt_local = datetime.strptime(local_dt_str, fmt)
            return dt_local.replace(tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)
        except Exception:
            pass
    raise ValueError(f"Could not parse fixtureDateTime '{local_dt_str}'")

def key_from_date_and_teams(date_str: str, home: str, away: str) -> str:
    """Canonical match key (date-only + teams)."""
    for fmt in ("%d/%m/%y %H:%M", "%d/%m/%Y %H:%M", "%d/%m/%y", "%d/%m/%Y"):
        try:
            d = datetime.strptime(date_str, fmt)
            return f"{d.strftime('%Y%m%d')}|{clean_team(home)}|{clean_team(away)}"
        except Exception:
            pass
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
    log(f"[INFO] Fixtures: {len(fixtures)} | Results: {len(results)}")

    # 🔒 SAFETY: if the API returns nothing, do not touch the ICS/state
    if len(fixtures) == 0:
        log("[WARN] No fixtures returned; leaving existing ICS untouched.")
        return

    # Build result lookup
    res_map = {}
    for r in results:
        date_str = r.get("resultDateTime") or r.get("fixtureDateTime") or r.get("date") or ""
        home     = r.get("homeTeam") or r.get("home") or ""
        away     = r.get("awayTeam") or r.get("away") or ""
        hs       = r.get("homeScore") or r.get("homeGoals")
        as_      = r.get("awayScore") or r.get("awayGoals")
        res_map[key_from_date_and_teams(date_str, home, away)] = {"hs": hs, "as": as_}

    # Safe sort (won’t crash if one row has odd date)
    def safe_key(fx):
        try:
            return parse_fixture_dt_local_to_utc(fx.get("fixtureDateTime") or fx.get("date") or "")
        except Exception:
            return datetime.max.replace(tzinfo=timezone.utc)
    fixtures.sort(key=safe_key)

    state = load_state()
    prev_uids = set(state.keys())
    seen_uids = set()
    added, updated, removed = [], [], []

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//PooleTown//U18 Fixtures via FullTimeAPI//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    # Calendar-level X-properties (RFC-safe names)
    for link in LINKS:
        label, url = link.split(":", 1)
        safe_name = label.strip().upper().replace(" ", "-").replace("/", "-")
        lines.append(f"X-{safe_name}:{url.strip()}")

    built = 0
    for fx in fixtures:
        try:
            f_date_local = fx.get("fixtureDateTime") or fx.get("date") or ""
            home = (fx.get("homeTeam") or "").strip()
            away = (fx.get("awayTeam") or "").strip()
            venue = (fx.get("location") or fx.get("ground") or "").strip()
            comp  = (fx.get("competition") or "").strip()

            start_utc = parse_fixture_dt_local_to_utc(f_date_local)
            end_utc   = start_utc + EVENT_DURATION
            uid       = make_uid(start_utc, home, away)
            seen_uids.add(uid)

            us_home   = TEAM_NAME.lower() in home.lower()
            opponent  = away if us_home else home

            # Result merge
            r = res_map.get(key_from_date_and_teams(f_date_local, home, away))
            hs = str(r["hs"]).strip() if r and r.get("hs") is not None else None
            as_ = str(r["as"]).strip() if r and r.get("as") is not None else None

            if hs is not None and as_ is not None:
                summary = f"{TEAM_NAME} {hs}–{as_} {opponent}" if us_home else f"{opponent} {hs}–{as_} {TEAM_NAME}"
            else:
                summary = f"{TEAM_NAME} vs {opponent}" if us_home else f"{opponent} vs {TEAM_NAME}"

            desc_bits = [f"{home} vs {away}"]
            if comp:  desc_bits.append(f"Competition: {comp}")
            if venue: desc_bits.append(f"Venue: {venue}")
            if hs is not None and as_ is not None:
                desc_bits.append(f"Result: {home} {hs}–{as_} {away}")
            desc_bits.extend(LINKS)
            description = "\\n".join(esc(x) for x in desc_bits)

            # Change detection snapshot
            snapshot = {
                "ko": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "home": home, "away": away,
                "venue": venue, "comp": comp,
                "hs": hs, "as": as_,
            }
            prev = state.get(uid, {}).get("snapshot")

            if prev is None:
                added.append(f"• {fmt_local(start_utc)} — {home} vs {away} ({venue})")
            else:
                deltas = []
                if prev.get("ko") != snapshot["ko"]:
                    deltas.append("time")
                if prev.get("venue") != venue:
                    deltas.append("venue")
                if prev.get("comp") != comp:
                    deltas.append("competition")
                if (prev.get("hs"), prev.get("as")) != (hs, as_):
                    if hs is not None and as_ is not None:
                        deltas.append(f"score {home} {hs}–{as_} {away}")
                    else:
                        deltas.append("score cleared")
                if deltas:
                    updated.append(f"• {fmt_local(start_utc)} — {home} vs {away} ({', '.join(deltas)})")

            # SEQUENCE bump if fixture+result composite changed
            fingerprint = hashlib.sha256(json.dumps({"fx": fx, "res": r}, sort_keys=True, default=str).encode()).hexdigest()
            seq = state.get(uid, {}).get("seq", 0)
            if state.get(uid, {}).get("fp") not in (None, fingerprint):
                seq += 1
            state[uid] = {"seq": seq, "fp": fingerprint, "snapshot": snapshot}

            now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
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
            log(f"[WARN] Skipping fixture due to error: {e}")
            log("[WARN] Offending item was:\n" + json.dumps(fx, indent=2, ensure_ascii=False))

    # Removed fixtures (were in state, not seen now)
    for uid in sorted(prev_uids - seen_uids):
        prev = state.get(uid, {}).get("snapshot", {})
        try:
            dt = datetime.strptime(prev.get("ko",""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            removed.append(f"• {fmt_local(dt)} — {prev.get('home','?')} vs {prev.get('away','?')}")
        except Exception:
            removed.append(f"• {prev.get('home','?')} vs {prev.get('away','?')}")

    # ⛔ Nothing to write? Keep previous file.
    if built == 0:
        log("[WARN] Built 0 events; leaving existing ICS untouched.")
        return

    # Write ICS
    lines.append("END:VCALENDAR")
    with open(OUTPUT, "wb") as f:
        f.write(crlf_join(lines).encode("utf-8"))
    save_state(state)
    log(f"[INFO] Wrote {OUTPUT} with {built} events.")

    # Write notify.txt if there are changes
    notify_sections = []
    if added:
        notify_sections.append("<b>➕ Added fixtures</b>\n" + "\n".join(tg_escape(x) for x in added))
    if updated:
        notify_sections.append("<b>✏️ Updated fixtures</b>\n" + "\n".join(tg_escape(x) for x in updated))
    if removed:
        notify_sections.append("<b>❌ Removed fixtures</b>\n" + "\n".join(tg_escape(x) for x in removed))

    if notify_sections:
        with open(NOTIFY_FILE, "w", encoding="utf-8") as nf:
            nf.write("\n\n".join(notify_sections))
    else:
        if os.path.exists(NOTIFY_FILE):
            os.remove(NOTIFY_FILE)

if __name__ == "__main__":
    main()
