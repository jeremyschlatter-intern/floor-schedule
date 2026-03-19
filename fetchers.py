"""
Data fetchers for congressional schedule sources.
Pulls from House floor XML, Senate hearings XML, Senate floor schedule,
and Congress.gov API.
"""

import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import logging
import pytz

logger = logging.getLogger(__name__)

CONGRESS_API_KEY = os.environ.get(
    "CONGRESS_API_KEY", "DEMO_KEY"
)
ET_TZ = pytz.timezone("America/New_York")


@dataclass
class ScheduleEvent:
    """Unified event representation for any congressional schedule item."""
    title: str
    date: date
    time: Optional[str]  # e.g. "10:00 AM" or None for TBD
    chamber: str  # "House" or "Senate"
    event_type: str  # "floor", "hearing", "markup", "meeting"
    committee: Optional[str] = None
    subcommittee: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    bill_numbers: list = field(default_factory=list)
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    event_id: Optional[str] = None
    week_of: bool = False  # True for items that span the whole week (no specific day)

    @property
    def sort_key(self):
        """Sort by date, then time (TBD items last within a day), then chamber."""
        time_sort = self.time or "99:99"
        try:
            t = datetime.strptime(time_sort.strip(), "%I:%M %p")
            time_sort = t.strftime("%H:%M")
        except ValueError:
            pass
        return (self.date, time_sort, self.chamber, self.title)

    @property
    def datetime_start(self) -> datetime:
        """Return a datetime combining date and time."""
        if self.time:
            try:
                t = datetime.strptime(self.time.strip(), "%I:%M %p")
                return datetime.combine(self.date, t.time())
            except ValueError:
                pass
        return datetime.combine(self.date, datetime.min.time())

    def to_dict(self):
        return {
            "title": self.title,
            "date": self.date.isoformat(),
            "time": self.time,
            "chamber": self.chamber,
            "event_type": self.event_type,
            "committee": self.committee,
            "subcommittee": self.subcommittee,
            "location": self.location,
            "description": self.description,
            "bill_numbers": self.bill_numbers,
            "source_url": self.source_url,
            "source_name": self.source_name,
            "event_id": self.event_id,
            "week_of": self.week_of,
        }


# Source status tracking
_source_status = {}


def get_source_status() -> dict:
    """Return status of each data source (ok/error/count)."""
    return dict(_source_status)


def _mark_source(name: str, ok: bool, count: int = 0, error: str = ""):
    _source_status[name] = {"ok": ok, "count": count, "error": error}


def get_current_week_monday(offset_weeks: int = 0):
    """Get the Monday of the current (or offset) week."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday + timedelta(weeks=offset_weeks)


def _utc_to_et(utc_dt: datetime) -> datetime:
    """Convert a UTC datetime to Eastern Time properly (handles EST/EDT)."""
    if utc_dt.tzinfo is None:
        utc_dt = pytz.utc.localize(utc_dt)
    return utc_dt.astimezone(ET_TZ)


def _format_et_time(utc_dt: datetime) -> Optional[str]:
    """Convert UTC datetime to Eastern time string like '10:00 AM'."""
    et_dt = _utc_to_et(utc_dt)
    if et_dt.hour == 0 and et_dt.minute == 0:
        return None  # Midnight likely means time not set
    return et_dt.strftime("%-I:%M %p")


def _bill_url(bill_id: str) -> str:
    """Generate a Congress.gov URL for a bill identifier."""
    bill_id = bill_id.strip()
    # Parse the bill type and number
    patterns = [
        (r"H\.?\s*Res\.?\s*(\d+)", "house-resolution"),
        (r"H\.?\s*J\.?\s*Res\.?\s*(\d+)", "house-joint-resolution"),
        (r"H\.?\s*Con\.?\s*Res\.?\s*(\d+)", "house-concurrent-resolution"),
        (r"S\.?\s*Res\.?\s*(\d+)", "senate-resolution"),
        (r"S\.?\s*J\.?\s*Res\.?\s*(\d+)", "senate-joint-resolution"),
        (r"S\.?\s*Con\.?\s*Res\.?\s*(\d+)", "senate-concurrent-resolution"),
        (r"H\.?\s*R\.?\s*(\d+)", "house-bill"),
        (r"S\.?\s*(\d+)", "senate-bill"),
    ]
    for pattern, bill_type in patterns:
        m = re.match(pattern, bill_id, re.IGNORECASE)
        if m:
            num = m.group(1)
            return f"https://www.congress.gov/bill/119th-congress/{bill_type}/{num}"
    return f"https://www.congress.gov/search?q={bill_id}"


def fetch_house_floor_xml(week_offset: int = 0) -> list[ScheduleEvent]:
    """Fetch House floor schedule from docs.house.gov XML feed."""
    events = []
    monday = get_current_week_monday(week_offset)
    week_str = monday.strftime("%Y%m%d")

    url = f"https://docs.house.gov/floor/Download.aspx?file=/billsthisweek/{week_str}/{week_str}.xml"
    logger.info(f"Fetching House floor XML: {url}")

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch House floor XML: {e}")
        _mark_source("House Floor (Clerk)", False, error=str(e))
        return events

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        logger.error(f"Failed to parse House floor XML: {e}")
        _mark_source("House Floor (Clerk)", False, error="XML parse error")
        return events

    week_date_str = root.get("week-date", "")
    try:
        week_date = datetime.strptime(week_date_str, "%Y-%m-%d").date()
    except ValueError:
        week_date = monday

    for category in root.findall("category"):
        category_type = category.get("type", "Items")

        for item in category.findall(".//floor-item"):
            if item.get("remove-date"):
                continue

            legis_num = (item.findtext("legis-num") or "").strip()
            floor_text = (item.findtext("floor-text") or "").strip()

            if not legis_num and not floor_text:
                continue

            title = f"{legis_num} - {floor_text}" if legis_num else floor_text
            if len(title) > 200:
                title = title[:197] + "..."
            bill_numbers = [legis_num] if legis_num else []

            doc_url = None
            for f in item.findall(".//file"):
                doc_url = f.get("doc-url")
                break

            # Floor items span the entire week - the exact day/time depends
            # on the Majority Leader's weekly plan. We mark them week_of=True
            # and display them in a separate "This Week on the Floor" section.
            # Map verbose category labels to shorter display names
            category_lower = category_type.lower()
            category_short = category_type
            if "suspension" in category_lower:
                category_short = "Suspension"
            elif "pursuant to a rule" in category_lower:
                category_short = "Pursuant to Rule"
            elif "special order" in category_lower:
                category_short = "Special Order"
            elif "may be considered" in category_lower:
                category_short = "May Be Considered"

            event = ScheduleEvent(
                title=title,
                date=week_date,
                time=None,
                chamber="House",
                event_type="floor",
                description=f"Category: {category_short}",
                bill_numbers=bill_numbers,
                week_of=True,
                source_url=doc_url or "https://docs.house.gov/floor/",
                source_name="House Clerk",
                event_id=item.get("id"),
            )
            events.append(event)

    _mark_source("House Floor (Clerk)", True, count=len(events))
    logger.info(f"Fetched {len(events)} House floor items")
    return events


def fetch_senate_floor_schedule(week_offset: int = 0) -> list[ScheduleEvent]:
    """Fetch Senate floor schedule from Senate Democrats site."""
    events = []
    url = "https://www.democrats.senate.gov/floor"
    logger.info(f"Fetching Senate floor schedule: {url}")

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Capitol Week Congressional Schedule Aggregator"
        })
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as e:
        logger.error(f"Failed to fetch Senate floor schedule: {e}")
        _mark_source("Senate Floor", False, error=str(e))
        return events

    monday = get_current_week_monday(week_offset)

    # Try to extract schedule text - the Senate Democrats page has
    # the floor schedule in a relatively structured format
    # Look for schedule content between common markers
    # This is a best-effort parse of the HTML
    import re as _re

    # Extract the main content area
    # Look for dates and associated text
    date_pattern = r'(Monday|Tuesday|Wednesday|Thursday|Friday),?\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})'

    matches = list(_re.finditer(date_pattern, html, _re.IGNORECASE))

    if not matches:
        logger.info("No dated entries found in Senate floor schedule")
        _mark_source("Senate Floor", True, count=0)
        return events

    current_year = date.today().year
    month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }

    for i, match in enumerate(matches):
        day_name = match.group(1)
        month_name = match.group(2).lower()
        day_num = int(match.group(3))
        month_num = month_map.get(month_name, 0)
        if not month_num:
            continue

        try:
            schedule_date = date(current_year, month_num, day_num)
        except ValueError:
            continue

        # Only include this week
        if schedule_date < monday or schedule_date > monday + timedelta(days=6):
            continue

        # Extract text between this match and the next
        start_pos = match.end()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else start_pos + 2000

        # Strip HTML tags and clean up the text
        segment = html[start_pos:end_pos]
        segment = _re.sub(r'<[^>]+>', ' ', segment)
        # Decode HTML entities
        import html as html_module
        segment = html_module.unescape(segment)
        # Clean whitespace
        segment = _re.sub(r'\s+', ' ', segment).strip()
        # Remove common boilerplate
        segment = _re.sub(r'^\s*[,.:;]\s*', '', segment)

        if not segment or len(segment) < 10:
            continue

        # Create a concise title from the schedule text
        # Try to extract the key action (e.g. "convene at 3:00pm", "resume consideration of...")
        title_text = segment[:200] + "..." if len(segment) > 200 else segment
        # Clean up the title - extract the first meaningful sentence
        sentences = _re.split(r'(?<=[.!])\s+', title_text)
        if sentences:
            # Use the first sentence, capped at 150 chars
            first = sentences[0].strip()
            if len(first) > 150:
                first = first[:147] + "..."
            title_text = first

        event = ScheduleEvent(
            title=f"Senate Floor - {day_name}, {match.group(2)} {day_num}",
            date=schedule_date,
            time=None,
            chamber="Senate",
            event_type="floor",
            description=segment[:1000],
            week_of=True,
            source_url="https://www.democrats.senate.gov/floor",
            source_name="Senate Democrats",
        )
        events.append(event)

    _mark_source("Senate Floor", True, count=len(events))
    logger.info(f"Fetched {len(events)} Senate floor events")
    return events


def fetch_senate_hearings_xml(week_offset: int = 0) -> list[ScheduleEvent]:
    """Fetch Senate committee hearings from senate.gov XML feed."""
    events = []
    url = "https://www.senate.gov/general/committee_schedules/hearings.xml"
    logger.info(f"Fetching Senate hearings XML: {url}")

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch Senate hearings XML: {e}")
        _mark_source("Senate Hearings (XML)", False, error=str(e))
        return events

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        logger.error(f"Failed to parse Senate hearings XML: {e}")
        _mark_source("Senate Hearings (XML)", False, error="XML parse error")
        return events

    monday = get_current_week_monday(week_offset)

    for meeting in root.findall("meeting"):
        date_str = meeting.findtext("date_iso_8601", "").strip()
        if not date_str:
            continue

        try:
            meeting_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if meeting_date < monday or meeting_date > monday + timedelta(days=6):
            continue

        committee = meeting.findtext("committee", "").strip()
        subcommittee = meeting.findtext("sub_cmte", "").strip() or None
        time_str = meeting.findtext("time", "").strip() or None
        room = meeting.findtext("room", "").strip() or None
        matter = meeting.findtext("matter", "").strip() or None

        bill_numbers = []
        seen_bills = set()
        docs_elem = meeting.find("Documents")
        if docs_elem is not None:
            for doc in docs_elem.findall("AssociatedDocument"):
                prefix = doc.get("document_prefix", "")
                num = doc.get("document_num", "")
                if prefix and num:
                    if prefix == "SN":
                        bill_id = f"S. {num}"
                    elif prefix == "HR":
                        bill_id = f"H.R. {num}"
                    elif prefix == "PN":
                        bill_id = f"PN {num}"
                    else:
                        bill_id = f"{prefix} {num}"
                    if bill_id not in seen_bills:
                        seen_bills.add(bill_id)
                        bill_numbers.append(bill_id)

        display_committee = committee
        if subcommittee:
            display_committee = f"{committee} - {subcommittee}"

        short_matter = matter
        if matter and len(matter) > 150:
            short_matter = matter[:147] + "..."

        title = f"{display_committee}: {short_matter}" if short_matter else display_committee

        event = ScheduleEvent(
            title=title,
            date=meeting_date,
            time=time_str,
            chamber="Senate",
            event_type="hearing",
            committee=committee,
            subcommittee=subcommittee,
            location=room,
            description=matter,
            bill_numbers=bill_numbers,
            source_url="https://www.senate.gov/committees/hearings_meetings.htm",
            source_name="Senate.gov",
            event_id=meeting.findtext("identifier", "").strip(),
        )
        events.append(event)

    _mark_source("Senate Hearings (XML)", True, count=len(events))
    logger.info(f"Fetched {len(events)} Senate hearing events")
    return events


def _fetch_meeting_detail(detail_url: str) -> Optional[dict]:
    """Fetch a single meeting detail from Congress.gov API. Used in thread pool."""
    try:
        resp = requests.get(
            f"{detail_url}&api_key={CONGRESS_API_KEY}",
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("committeeMeeting", {})
    except Exception:
        return None


def _parse_meeting_detail(detail: dict, monday: date, sunday: date) -> Optional[ScheduleEvent]:
    """Parse a meeting detail dict into a ScheduleEvent, or None if not this week."""
    date_str = detail.get("date", "")
    if not date_str:
        return None

    try:
        meeting_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        meeting_date = meeting_dt.date()
    except ValueError:
        return None

    if meeting_date < monday or meeting_date > sunday:
        return None

    chamber = detail.get("chamber", "")
    title = detail.get("title", "Committee Meeting")
    if len(title) > 200:
        title = title[:197] + "..."
    meeting_type = detail.get("type", "Meeting")
    status = detail.get("meetingStatus", "")

    is_cancelled = status.lower() in ("cancelled", "postponed")

    committees = detail.get("committees", [])
    committee_name = committees[0].get("name", "") if committees else ""

    location_data = detail.get("location", {})
    location = None
    if location_data:
        building = location_data.get("building", "")
        room = location_data.get("room", "")
        # Skip redacted/placeholder room numbers (e.g. "----------")
        if room and re.match(r'^[-]+$', room.strip()):
            room = ""
        location = f"{building}, Room {room}" if building and room else building or room or None

    # Proper UTC to ET conversion
    time_str = _format_et_time(meeting_dt)

    event_type = "hearing"
    if "markup" in meeting_type.lower():
        event_type = "markup"
    elif "meeting" in meeting_type.lower():
        event_type = "meeting"

    # Override: if the title strongly suggests it's a hearing, classify it as such
    title_lower = title.lower()
    if event_type == "meeting" and any(
        pattern in title_lower for pattern in
        ["hearings to examine", "hearing to examine", "hearing on", "hearing -",
         "confirmation hearing", "nomination of", "threats assessment hearing"]
    ):
        event_type = "hearing"

    event_id = detail.get("eventId", "")
    congress_url = (
        f"https://www.congress.gov/event/119th-congress/{chamber.lower()}-event/{event_id}"
        if event_id else None
    )

    # Prepend cancelled/postponed status to title
    if is_cancelled:
        title = f"[{status.upper()}] {title}"

    return ScheduleEvent(
        title=title,
        date=meeting_date,
        time=time_str,
        chamber=chamber,
        event_type=event_type,
        committee=committee_name,
        location=location,
        description=f"{meeting_type} - {status}" if status else meeting_type,
        source_url=congress_url or "https://www.congress.gov/committee-schedule",
        source_name="Congress.gov",
        event_id=str(event_id),
    )


def fetch_congress_api_meetings(week_offset: int = 0) -> list[ScheduleEvent]:
    """Fetch committee meetings from Congress.gov API using parallel requests."""
    events = []
    monday = get_current_week_monday(week_offset)
    sunday = monday + timedelta(days=6)

    # Use date filtering to get only this week's meetings
    from_dt = monday.strftime("%Y-%m-%dT00:00:00Z")
    to_dt = (sunday + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    url = (
        f"https://api.congress.gov/v3/committee-meeting"
        f"?api_key={CONGRESS_API_KEY}"
        f"&fromDateTime={from_dt}&toDateTime={to_dt}"
        f"&limit=250&format=json"
    )
    logger.info(f"Fetching Congress.gov API meetings for {monday} to {sunday}")

    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.error(f"Failed to fetch Congress.gov API: {e}")
        _mark_source("Congress.gov API", False, error=str(e))
        return events

    meetings = data.get("committeeMeetings", [])
    detail_urls = [m.get("url") for m in meetings if m.get("url")]

    logger.info(f"Fetching {len(detail_urls)} meeting details in parallel")
    details = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_meeting_detail, url): url for url in detail_urls}
        for future in as_completed(futures):
            detail = future.result()
            if detail:
                details.append(detail)

    for detail in details:
        event = _parse_meeting_detail(detail, monday, sunday)
        if event:
            events.append(event)

    _mark_source("Congress.gov API", True, count=len(events))
    logger.info(f"Fetched {len(events)} Congress.gov API events (from {len(details)} details)")
    return events


def _normalize_time(time_raw: str) -> str:
    """Normalize time string for dedup comparison."""
    time_raw = time_raw.strip()
    for fmt in ("%I:%M %p", "%I:%M%p", "%-I:%M %p"):
        try:
            t = datetime.strptime(time_raw, fmt)
            return t.strftime("%H:%M")
        except ValueError:
            continue
    return time_raw.lower()


def _normalize_committee(name: str) -> str:
    """Normalize committee name for dedup comparison."""
    name = re.sub(r'\s+', ' ', name.lower().strip())
    # Remove chamber prefix
    name = re.sub(r'^(house|senate)\s+', '', name)
    # Remove "subcommittee on..." suffix for broader matching
    name = re.sub(r'\s*subcommittee\s+on\s+.*$', '', name)
    # Remove "committee on" prefix
    name = re.sub(r'^committee\s+on\s+', '', name)
    return name.strip()


def _dedup_key(event: ScheduleEvent) -> str:
    """Generate a dedup key for an event."""
    if event.event_type == "floor":
        bill = event.bill_numbers[0] if event.bill_numbers else event.title[:40]
        return f"floor|{event.date}|{event.chamber}|{bill.lower().strip()}"

    committee_norm = _normalize_committee(event.committee or event.title[:40])
    time_norm = _normalize_time(event.time or "")
    return f"committee|{event.date}|{time_norm}|{event.chamber}|{committee_norm[:50]}"


def _is_duplicate(event: ScheduleEvent, seen_keys: set, seen_events: list) -> bool:
    """Check if event is a duplicate, using both exact key match and fuzzy matching."""
    key = _dedup_key(event)
    if key in seen_keys:
        return True

    # Fuzzy match: same date + time + chamber but different committee normalization
    if event.time:
        time_norm = _normalize_time(event.time)
        committee_norm = _normalize_committee(event.committee or "")
        for existing in seen_events:
            if (existing.date == event.date
                    and existing.chamber == event.chamber
                    and existing.time
                    and _normalize_time(existing.time) == time_norm):
                existing_comm = _normalize_committee(existing.committee or "")
                # Check if one committee name contains the other
                if (committee_norm and existing_comm
                        and (committee_norm in existing_comm or existing_comm in committee_norm)):
                    return True
    return False


def fetch_all_events(week_offset: int = 0) -> list[ScheduleEvent]:
    """Fetch events from all sources and return sorted, deduplicated list."""
    global _source_status
    _source_status = {}
    all_events = []

    all_events.extend(fetch_house_floor_xml(week_offset))
    all_events.extend(fetch_senate_floor_schedule(week_offset))
    all_events.extend(fetch_senate_hearings_xml(week_offset))
    all_events.extend(fetch_congress_api_meetings(week_offset))

    def source_priority(e):
        if e.chamber == "Senate" and e.source_name == "Senate.gov":
            return 0
        if e.source_name == "Congress.gov":
            return 1
        return 2

    seen_keys = set()
    deduped = []
    for event in sorted(all_events, key=source_priority):
        if not _is_duplicate(event, seen_keys, deduped):
            seen_keys.add(_dedup_key(event))
            deduped.append(event)

    deduped.sort(key=lambda e: e.sort_key)
    logger.info(f"Total events after dedup: {len(deduped)}")
    return deduped


def get_week_range(week_offset: int = 0):
    """Return (monday, sunday) for the current (or offset) week."""
    monday = get_current_week_monday(week_offset)
    sunday = monday + timedelta(days=6)
    return monday, sunday
