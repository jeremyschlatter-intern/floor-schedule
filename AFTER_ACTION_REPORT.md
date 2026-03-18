# Capitol Week: After Action Report

## What I Built

**Capitol Week** is a web tool that gives Hill staffers a unified view of the weekly congressional schedule — floor action, committee hearings, markups, and meetings — aggregated from four official sources into a single page with filtering, search, and iCal export.

Live at: `https://mono-wichita-indexed-bases.trycloudflare.com/`

## The Problem

Congressional staffers currently check 3-4 separate government websites to piece together the week's schedule:
- House Clerk (docs.house.gov) for floor bills
- Senate Democrats site for Senate floor schedule
- Senate.gov for Senate committee hearings
- Congress.gov for House committee hearings and meetings

Each source has a different format, different update cadence, and no cross-referencing. Staffers stitch this together manually, often across multiple browser tabs and shared spreadsheets.

## My Process

### Phase 1: Research and Data Source Discovery

I started by studying the [Congressional Data Wiki](https://github.com/unitedstates/congress/wiki) and testing each data source:

- **House Clerk XML** — Clean, structured XML at a predictable URL pattern (`/billsthisweek/{YYYYMMDD}/{YYYYMMDD}.xml`). Lists bills scheduled for floor action that week, categorized by procedure type (suspension, rule, etc.).
- **Senate hearings XML** — Well-structured feed at `senate.gov/general/committee_schedules/hearings.xml` with dates, times, committees, rooms, and bill numbers.
- **Congress.gov API** — REST API with meeting listings, but requires individual detail fetches for each meeting. Initially very slow (serial requests for 250+ meetings).
- **Senate Democrats floor page** — HTML page with semi-structured schedule text. Required regex-based parsing.

**Obstacle:** The Congress.gov API initially required fetching detail pages one-by-one, causing 30+ second page loads. I parallelized the requests using `ThreadPoolExecutor` with 10 workers and added date-range filtering to reduce the result set. Load time dropped to ~4 seconds.

### Phase 2: Core Implementation

Built a Flask application with:
- `fetchers.py` — Four data fetchers with proper error handling and source status tracking
- `app.py` — Web routes for the main page, JSON API, and iCal feeds
- `ical_generator.py` — iCal file generation with proper timezone handling
- Client-side filtering, search, and keyboard shortcuts

**Obstacle:** The Chrome browser I was told to use for visual testing was actually on a different machine on the local network. `localhost` URLs wouldn't work. I solved this by setting up a Cloudflare tunnel (`cloudflared tunnel --url http://localhost:5050`) to expose the local server via a public URL.

### Phase 3: DC Agent Feedback Loop

I created an agent teammate playing the role of Daniel Schuman (the DC legislative tech expert who originated the project idea) to give iterative feedback. Three rounds of review produced 20+ actionable items. Key ones:

**Round 1 (14 items):**
- Missing Senate floor schedule entirely → Added Senate Democrats site scraper
- Hardcoded EDT timezone conversion (`hour - 4`) → Replaced with `pytz` for proper EST/EDT handling
- All House floor items dumped on Monday → Identified that floor items don't have specific days (leadership decides daily). Created a separate "This Week on the Floor" grid section.
- No search or week navigation → Added both

**Round 2 (5 items):**
- Congress.gov API not date-filtered (missing events in busy weeks) → Added `fromDateTime`/`toDateTime` parameters
- Senate floor HTML entities rendering incorrectly → Added `html.unescape()`

**Round 3 (9 items):**
- **Duplicate events** between Senate.gov and Congress.gov (same hearing listed twice with different committee name formats) → Built fuzzy dedup that normalizes committee names by stripping chamber prefixes and "Subcommittee on..." suffixes, then checks for substring containment
- **Type mismatch**: Congress.gov labels many Senate hearings as "meeting" → Added title-based inference ("Hearings to examine..." → classify as hearing)
- **Verbose category labels** on floor cards ("ITEMS THAT MAY BE CONSIDERED UNDER SUSPENSION OF THE RULES") → Mapped to short labels ("Suspension")
- **30-minute cache too stale** → Reduced to 10 minutes
- **Cancelled events silently hidden** → Now shown with visual badge

### Phase 4: Final Polish

After addressing all feedback, the DC agent gave final approval: "Ship it. It's polished, it's honest about its sources, and it solves a real problem."

## Technical Decisions

| Decision | Why |
|----------|-----|
| Flask over static site | Need server-side data fetching and caching; iCal generation requires Python |
| 4-source aggregation | No single source has complete data for both chambers |
| Fuzzy dedup | Different sources use different committee name formats for the same hearing |
| `week_of` flag for floor items | Floor bills don't have specific days — treating them as all-day Monday events was misleading |
| 10-min cache TTL | Balance between freshness (schedules change frequently) and not hammering data sources |
| Cloudflare tunnel | Remote Chrome browser couldn't access localhost |

## What Went Well

1. **The "This Week on the Floor" section** — The DC agent specifically called this out as something not done well in existing tools. Separating week-spanning floor items from daily-scheduled committee events matches how Hill staffers actually think about the schedule.

2. **Four-source aggregation with status indicators** — Having all sources visible with green checkmarks and item counts builds trust. A schedule tool lives or dies on whether people trust its data.

3. **Fuzzy dedup** — The hardest technical problem. Senate.gov calls a committee "Commerce, Science, and Transportation" while Congress.gov calls it "Senate Commerce, Science, and Transportation Subcommittee on Telecommunications and Media." Getting these to match required normalizing committee names by stripping chamber prefixes and subcommittee suffixes, then doing substring comparison.

## What Was Hard

1. **Congress.gov API performance** — The API requires individual detail fetches for each meeting. Even with parallelization and date filtering, this is the slowest part of the data pipeline (~3 seconds for 50-60 meetings).

2. **Senate floor schedule parsing** — The Democrats Senate site has semi-structured HTML with no consistent API. Regex-based extraction of dates and schedule text is fragile and will break when the page format changes.

3. **Cross-source deduplication** — Different sources have different committee names, different time formats ("10:00 AM" vs "10:00AM"), and different event type classifications for the same hearing.

4. **Remote Chrome testing** — Discovering that the Chrome browser was on a different machine required creative problem-solving (Cloudflare tunnel) rather than the standard localhost workflow.

## Known Limitations

- **No real-time alerts** — The tool refreshes on page load but doesn't push notifications when schedules change. The DC agent identified this as the gap between "useful" and "indispensable."
- **Senate floor parsing is fragile** — Regex-based HTML scraping will break when the Senate Democrats site changes its format.
- **Week navigation capped at +/- 4 weeks** — No visual indication when you hit the boundary.
- **No House committee data from docs.house.gov** — The House Committee Calendar was in the original plan but not implemented; Congress.gov API covers most House committee events.

## Architecture

```
User Browser
    │
    ├── GET /                    → Server-rendered HTML (Jinja2)
    ├── GET /calendar.ics        → iCal feed (full week)
    ├── GET /calendar/{date}.ics → iCal feed (single day)
    └── GET /api/events          → JSON API
         │
         └── get_events(week_offset)
              │ (10-min cache)
              ├── fetch_house_floor_xml()        → docs.house.gov XML
              ├── fetch_senate_floor_schedule()  → democrats.senate.gov HTML
              ├── fetch_senate_hearings_xml()    → senate.gov XML
              └── fetch_congress_api_meetings()  → Congress.gov API (parallel)
                   │
                   └── dedup (fuzzy committee matching)
                        │
                        └── sorted events list
```

## Team

This project was completed by a single Claude Code agent with:
- A DC agent teammate (simulated Daniel Schuman persona) for three rounds of iterative feedback
- Chrome browser automation for visual testing and UI refinement

## Files

| File | Purpose |
|------|---------|
| `fetchers.py` | Four data fetchers, dedup logic, source status tracking |
| `app.py` | Flask routes, caching, template context |
| `ical_generator.py` | iCal (.ics) file generation |
| `templates/index.html` | Main page template (Jinja2) |
| `static/style.css` | Responsive styles with chamber color coding |
| `static/app.js` | Client-side filtering, search, keyboard shortcuts |
| `requirements.txt` | Python dependencies |
| `plan.md` | Original architecture plan |
