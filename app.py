"""
Capitol Week - Unified Congressional Floor & Committee Schedule
Flask application serving the web interface and iCal feeds.
"""

import logging
import time
from datetime import date, datetime, timedelta
from flask import Flask, render_template, request, Response, jsonify
from fetchers import (
    fetch_all_events, get_week_range, get_source_status, get_current_week_monday,
    ScheduleEvent, _bill_url,
)
from ical_generator import create_calendar, calendar_to_bytes, filter_events

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Cache per week_offset
_cache = {}
CACHE_TTL = 1800  # 30 min


def get_events(week_offset: int = 0) -> list[ScheduleEvent]:
    """Get events with caching, keyed by week offset."""
    now = time.time()
    entry = _cache.get(week_offset)
    if entry and now - entry["fetched_at"] < CACHE_TTL and entry["events"]:
        return entry["events"]

    logger.info(f"Refreshing event data (week_offset={week_offset})...")
    events = fetch_all_events(week_offset)
    _cache[week_offset] = {"events": events, "fetched_at": now}
    return events


@app.template_filter("bill_url")
def bill_url_filter(bill_id):
    """Jinja filter for generating bill URLs."""
    return _bill_url(bill_id)


@app.route("/")
def index():
    """Main page - unified schedule view."""
    week_offset = request.args.get("week", 0, type=int)
    # Clamp to reasonable range
    week_offset = max(-4, min(4, week_offset))

    events = get_events(week_offset)
    monday, sunday = get_week_range(week_offset)
    source_status = get_source_status()

    # Separate week-of items (floor schedule) from daily items
    floor_items = [e.to_dict() for e in events if e.week_of]
    daily_events = [e for e in events if not e.week_of]

    # Group daily events by date
    days = []
    for i in range(5):  # Mon-Fri
        day = monday + timedelta(days=i)
        day_events = [e for e in daily_events if e.date == day]
        days.append({
            "date": day,
            "date_str": day.strftime("%A, %B %d"),
            "date_short": day.strftime("%a %m/%d"),
            "is_today": day == date.today(),
            "events": [e.to_dict() for e in day_events],
        })

    # Show weekends only if they have events
    for i in range(5, 7):
        day = monday + timedelta(days=i)
        day_events = [e for e in daily_events if e.date == day]
        if day_events:
            days.append({
                "date": day,
                "date_str": day.strftime("%A, %B %d"),
                "date_short": day.strftime("%a %m/%d"),
                "is_today": day == date.today(),
                "events": [e.to_dict() for e in day_events],
            })

    committees = sorted(set(e.committee for e in events if e.committee))
    event_types = sorted(set(e.event_type for e in events))

    week_label = f"{monday.strftime('%B %d')} - {sunday.strftime('%B %d, %Y')}"
    cache_entry = _cache.get(week_offset, {})
    last_updated = (
        datetime.fromtimestamp(cache_entry.get("fetched_at", 0)).strftime("%I:%M %p")
        if cache_entry.get("fetched_at")
        else "Never"
    )

    # Check session status
    house_floor_count = sum(1 for e in events if e.chamber == "House" and e.event_type == "floor")
    senate_floor_count = sum(1 for e in events if e.chamber == "Senate" and e.event_type == "floor")
    session_info = {}
    if house_floor_count == 0:
        session_info["house"] = "No House floor activity scheduled this week"
    if senate_floor_count == 0:
        session_info["senate"] = "No Senate floor activity scheduled this week"

    return render_template(
        "index.html",
        days=days,
        floor_items=floor_items,
        committees=committees,
        event_types=event_types,
        week_label=week_label,
        last_updated=last_updated,
        total_events=len(events),
        week_offset=week_offset,
        source_status=source_status,
        session_info=session_info,
    )


@app.route("/api/events")
def api_events():
    """JSON API for events."""
    week_offset = request.args.get("week", 0, type=int)
    events = get_events(week_offset)
    chamber = request.args.get("chamber")
    event_type = request.args.get("type")
    committee = request.args.get("committee")

    filtered = filter_events(events, chamber=chamber, event_type=event_type, committee=committee)
    return jsonify([e.to_dict() for e in filtered])


@app.route("/calendar.ics")
def ical_feed():
    """iCal feed - full week schedule."""
    week_offset = request.args.get("week", 0, type=int)
    events = get_events(week_offset)
    chamber = request.args.get("chamber")
    event_type = request.args.get("type")
    committee = request.args.get("committee")

    filtered = filter_events(events, chamber=chamber, event_type=event_type, committee=committee)

    cal_name = "Capitol Week"
    if chamber:
        cal_name += f" - {chamber}"
    if event_type:
        cal_name += f" - {event_type.title()}"
    if committee:
        cal_name += f" - {committee}"

    cal = create_calendar(filtered, cal_name)
    resp = Response(
        calendar_to_bytes(cal),
        mimetype="text/calendar",
        headers={
            "Content-Disposition": "attachment; filename=capitol-week.ics",
            "Cache-Control": "public, max-age=900",  # 15 min cache for calendar apps
        },
    )
    return resp


@app.route("/calendar/<date_str>.ics")
def ical_day(date_str):
    """iCal feed for a specific day."""
    events = get_events()
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return "Invalid date format. Use YYYY-MM-DD.", 400

    day_events = filter_events(events, target_date=target)
    cal = create_calendar(day_events, f"Capitol Week - {target.strftime('%B %d, %Y')}")
    return Response(
        calendar_to_bytes(cal),
        mimetype="text/calendar",
        headers={
            "Content-Disposition": f"attachment; filename=capitol-week-{date_str}.ics",
            "Cache-Control": "public, max-age=900",
        },
    )


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5050
    app.run(debug=True, port=port, host="0.0.0.0")
