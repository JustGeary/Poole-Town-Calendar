import requests
from ics import Calendar, Event
from datetime import datetime, timedelta
import pytz
from uuid import uuid5, NAMESPACE_DNS
import re

# Timezone for UK
uk_tz = pytz.timezone("Europe/London")

# API endpoint
url = "https://faapi.jwhsolutions.co.uk/api/Results/972456211?teamName=Poole%20Town%20FC%20Wessex%20U18%20Colts"

# Fetch fixture data
response = requests.get(url)
response.raise_for_status()
data = response.json()

calendar = Calendar()

# Text cleaner for UID generation
def clean(text):
    return re.sub(r'\s+', ' ', text).strip().lower()

for fixture in data:
    fixture_datetime_str = fixture.get('fixtureDateTime')
    home_team = fixture.get('homeTeam')
    away_team = fixture.get('awayTeam')
    division = fixture.get('division')

    if not all([fixture_datetime_str, home_team, away_team, division]):
        continue

    try:
        naive_dt = datetime.strptime(fixture_datetime_str, "%d/%m/%y %H:%M")
        fixture_datetime = uk_tz.localize(naive_dt)
    except ValueError:
        continue

    end_time = fixture_datetime + timedelta(hours=1, minutes=45)

    # Build UID from date + teams + division
    fixture_date = naive_dt.strftime("%Y-%m-%d")
    uid_input = f"{fixture_date}-{clean(home_team)}-{clean(away_team)}-{clean(division)}"
    event_uid = str(uuid5(NAMESPACE_DNS, uid_input))

    # Set readable event title based on whether Poole Town is home or away
    if "poole town" in clean(home_team):
        opponent = away_team
        prefix = "Home vs"
    else:
        opponent = home_team
        prefix = "Away vs"
    event_title = f"{prefix} {opponent} ({division})"

    # Create calendar event
    event = Event()
    event.name = event_title
    event.begin = fixture_datetime
    event.end = end_time
    event.uid = event_uid
    event.description = f"Division: {division}"
    event.location = f"{home_team} Home Ground"

    calendar.events.add(event)

# Save .ics file
with open('poole_town_u18_colts_fixtures.ics', 'w', encoding='utf-8') as f:
    f.writelines(calendar)

print("✅ iCalendar file created with stable UIDs and formatted event titles.")
