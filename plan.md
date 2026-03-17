# Floor Schedule + Committee Calendar Unified Tool

## Problem
Congressional staff currently need to check multiple separate sources to track:
- House floor schedule (docs.house.gov)
- Senate floor activity (senate.gov, democrats.senate.gov)
- House committee hearings (docs.house.gov, congress.gov API)
- Senate committee hearings (senate.gov XML feed)

There's no single view that combines floor proceedings with committee schedules, and none of these sources provide iCal feeds for calendar integration.

## Solution
A web application ("Capitol Week") that:
1. **Aggregates** floor schedules and committee hearings from all official sources
2. **Presents** a unified, filterable weekly view
3. **Generates** iCal (.ics) files for calendar subscription
4. Links back to official sources (congress.gov, etc.) for each item

## Data Sources
| Source | URL | Format | Content |
|--------|-----|--------|---------|
| House Floor XML | docs.house.gov/floor/Download.aspx | XML | Bills scheduled for floor action this week |
| Senate Hearings XML | senate.gov/general/committee_schedules/hearings.xml | XML | Senate committee hearings with dates, rooms, topics |
| Congress.gov API | api.congress.gov/v3/committee-meeting | JSON | House committee meetings with detailed info |
| House Committee Calendar | docs.house.gov/Committee/Calendar/ByWeek.aspx | HTML | House committee calendar by week |

## Architecture
- **Python backend** (Flask) fetches and caches data from all sources
- **Frontend**: Clean, responsive HTML/CSS/JS with:
  - Weekly calendar grid view
  - List view with filters (chamber, committee, type)
  - Color-coded by chamber and event type
  - iCal download buttons (per-day, per-committee, full week)
- **iCal generation**: Python `icalendar` library
- Data auto-refreshes every 30 minutes

## Key Features
1. Unified view of floor + committee proceedings for the week
2. Filter by: chamber (House/Senate), event type (floor/hearing/markup), committee
3. iCal download: full week, by day, by chamber, by committee
4. Subscribe URL for live iCal feed
5. Links to official source pages and bill text
6. Mobile-responsive design
7. Color coding: House blue, Senate red, Floor items highlighted

## Tech Stack
- Python 3 + Flask
- icalendar library for .ics generation
- Vanilla JS + CSS (no heavy frameworks, fast loading)
- Data caching with TTL

## File Structure
```
floor-schedule/
├── app.py              # Flask app, routes, data fetching
├── fetchers.py         # Data source fetchers (XML, API, scraping)
├── ical_generator.py   # iCal file generation
├── templates/
│   └── index.html      # Main page template
├── static/
│   ├── style.css       # Styling
│   └── app.js          # Client-side interactivity
├── requirements.txt
└── plan.md
```
