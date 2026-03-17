"""
Capitol Week - Unified Congressional Floor & Committee Schedule
Flask application serving the web interface and iCal feeds.
"""

import logging
import time
from datetime import date, datetime, timedelta
from flask import Flask, render_template, request, Response, jsonify
from fetchers import fetch_all_events, get_week_range, ScheduleEvent
from ical_generator import create_calendar, calendar_to_bytes, filter_events

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Simple in-memory cache
_cache = {"events": [], "fetched_at": 0, "ttl": 1800}  # 30 min TTL


def get_events() -> list[ScheduleEvent]:
    """Get events with caching."""
    now = time.time()
    if now - _cache["fetched_at"] < _cache["ttl"] and _cache["events"]:
        return _cache["events"]

    logger.info("Refreshing event data from all sources...")
    events = fetch_all_events()
    _cache["events"] = events
    _cache["fetched_at"] = now
    return events


@app.route("/")
def index():
    """Main page - unified schedule view."""
    events = get_events()
    monday, sunday = get_week_range()

    # Group events by date
    days = []
    for i in range(7):
        day = monday + timedelta(days=i)
        day_events = [e for e in events if e.date == day]
        days.append({
            "date": day,
            "date_str": day.strftime("%A, %B %d"),
            "date_short": day.strftime("%a %m/%d"),
            "is_today": day == date.today(),
            "events": [e.to_dict() for e in day_events],
        })

    # Collect unique committees and types for filters
    committees = sorted(set(e.committee for e in events if e.committee))
    event_types = sorted(set(e.event_type for e in events))

    week_label = f"{monday.strftime('%B %d')} - {sunday.strftime('%B %d, %Y')}"
    last_updated = datetime.fromtimestamp(_cache["fetched_at"]).strftime("%I:%M %p") if _cache["fetched_at"] else "Never"

    return render_template(
        "index.html",
        days=days,
        committees=committees,
        event_types=event_types,
        week_label=week_label,
        last_updated=last_updated,
        total_events=len(events),
    )


@app.route("/api/events")
def api_events():
    """JSON API for events."""
    events = get_events()
    chamber = request.args.get("chamber")
    event_type = request.args.get("type")
    committee = request.args.get("committee")

    filtered = filter_events(events, chamber=chamber, event_type=event_type, committee=committee)
    return jsonify([e.to_dict() for e in filtered])


@app.route("/calendar.ics")
def ical_feed():
    """iCal feed - full week schedule."""
    events = get_events()
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
    return Response(
        calendar_to_bytes(cal),
        mimetype="text/calendar",
        headers={"Content-Disposition": f"attachment; filename=capitol-week.ics"},
    )


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
        headers={"Content-Disposition": f"attachment; filename=capitol-week-{date_str}.ics"},
    )


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5050
    app.run(debug=True, port=port, host="0.0.0.0")
