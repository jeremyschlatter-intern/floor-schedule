"""
iCal (.ics) file generation for congressional schedule events.
"""

from icalendar import Calendar, Event, vText
from datetime import datetime, timedelta
import pytz
from fetchers import ScheduleEvent

ET = pytz.timezone("America/New_York")


def create_calendar(events: list[ScheduleEvent], cal_name: str = "Capitol Week") -> Calendar:
    """Create an iCal calendar from a list of events."""
    cal = Calendar()
    cal.add("prodid", "-//Capitol Week//Congressional Schedule//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", cal_name)
    cal.add("x-wr-timezone", "America/New_York")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")

    for sched_event in events:
        ical_event = Event()

        # Title with chamber prefix
        prefix = "[H]" if sched_event.chamber == "House" else "[S]"
        type_tag = ""
        if sched_event.event_type == "floor":
            type_tag = " [Floor]"
        elif sched_event.event_type == "markup":
            type_tag = " [Markup]"

        ical_event.add("summary", f"{prefix}{type_tag} {sched_event.title}")

        # Date/time
        if sched_event.time:
            dt_start = ET.localize(sched_event.datetime_start)
            ical_event.add("dtstart", dt_start)
            ical_event.add("dtend", dt_start + timedelta(hours=1))  # Default 1hr duration
        else:
            ical_event.add("dtstart", sched_event.date)
            ical_event.add("dtend", sched_event.date + timedelta(days=1))

        # Location
        if sched_event.location:
            ical_event.add("location", sched_event.location)

        # Description
        desc_parts = []
        if sched_event.description:
            desc_parts.append(sched_event.description)
        if sched_event.committee:
            desc_parts.append(f"Committee: {sched_event.committee}")
        if sched_event.subcommittee:
            desc_parts.append(f"Subcommittee: {sched_event.subcommittee}")
        if sched_event.bill_numbers:
            desc_parts.append(f"Bills: {', '.join(sched_event.bill_numbers[:10])}")
            if len(sched_event.bill_numbers) > 10:
                desc_parts.append(f"  ...and {len(sched_event.bill_numbers) - 10} more")
        if sched_event.source_url:
            desc_parts.append(f"Source: {sched_event.source_url}")
        desc_parts.append(f"Via: {sched_event.source_name or 'Capitol Week'}")

        ical_event.add("description", "\n".join(desc_parts))

        # URL
        if sched_event.source_url:
            ical_event.add("url", sched_event.source_url)

        # UID
        uid = f"{sched_event.event_id or hash(sched_event.title)}-{sched_event.date.isoformat()}@capitolweek"
        ical_event.add("uid", uid)

        # Categories
        categories = [sched_event.chamber, sched_event.event_type.title()]
        if sched_event.committee:
            categories.append(sched_event.committee)
        ical_event.add("categories", categories)

        cal.add_component(ical_event)

    return cal


def calendar_to_bytes(cal: Calendar) -> bytes:
    """Serialize calendar to bytes for HTTP response."""
    return cal.to_ical()


def filter_events(
    events: list[ScheduleEvent],
    chamber: str = None,
    event_type: str = None,
    committee: str = None,
    target_date=None,
) -> list[ScheduleEvent]:
    """Filter events by various criteria."""
    filtered = events
    if chamber:
        filtered = [e for e in filtered if e.chamber.lower() == chamber.lower()]
    if event_type:
        filtered = [e for e in filtered if e.event_type.lower() == event_type.lower()]
    if committee:
        filtered = [e for e in filtered if committee.lower() in (e.committee or "").lower()]
    if target_date:
        filtered = [e for e in filtered if e.date == target_date]
    return filtered
