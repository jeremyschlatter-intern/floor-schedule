"""
Data fetchers for congressional schedule sources.
Pulls from House floor XML, Senate hearings XML, and Congress.gov API.
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import logging

logger = logging.getLogger(__name__)

CONGRESS_API_KEY = "CONGRESS_API_KEY"


@dataclass
class ScheduleEvent:
    """Unified event representation for any congressional schedule item."""
    title: str
    date: date
    time: Optional[str]  # e.g. "10:00 AM" or None for all-day
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

    @property
    def sort_key(self):
        """Sort by date, then time (all-day first), then chamber."""
        time_sort = self.time or "00:00"
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
        }


def get_current_week_monday():
    """Get the Monday of the current week."""
    today = date.today()
    return today - timedelta(days=today.weekday())


def fetch_house_floor_xml() -> list[ScheduleEvent]:
    """Fetch House floor schedule from docs.house.gov XML feed."""
    events = []
    monday = get_current_week_monday()
    week_str = monday.strftime("%Y%m%d")

    url = f"https://docs.house.gov/floor/Download.aspx?file=/billsthisweek/{week_str}/{week_str}.xml"
    logger.info(f"Fetching House floor XML: {url}")

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch House floor XML: {e}")
        # Try previous Monday if current week isn't available yet
        prev_monday = monday - timedelta(days=7)
        week_str = prev_monday.strftime("%Y%m%d")
        url = f"https://docs.house.gov/floor/Download.aspx?file=/billsthisweek/{week_str}/{week_str}.xml"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException:
            return events

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        logger.error(f"Failed to parse House floor XML: {e}")
        return events

    week_date_str = root.get("week-date", "")
    try:
        week_date = datetime.strptime(week_date_str, "%Y-%m-%d").date()
    except ValueError:
        week_date = monday

    # Categorize floor items by their category to assign approximate dates
    # Suspension items typically happen Mon-Tue, rule items Wed-Thu
    suspension_date = week_date  # Monday
    rule_date = week_date + timedelta(days=2)  # Wednesday
    today = date.today()

    for category in root.findall("category"):
        category_type = category.get("type", "Items")
        # Assign approximate date based on category
        if "suspension" in category_type.lower():
            item_date = suspension_date
        elif "rule" in category_type.lower() or "pursuant" in category_type.lower():
            item_date = rule_date
        else:
            item_date = week_date

        for item in category.findall(".//floor-item"):
            if item.get("remove-date"):
                continue

            legis_num = (item.findtext("legis-num") or "").strip()
            floor_text = (item.findtext("floor-text") or "").strip()

            if not legis_num and not floor_text:
                continue

            title = f"{legis_num} - {floor_text}" if legis_num else floor_text
            # Truncate very long floor text
            if len(title) > 200:
                title = title[:197] + "..."
            bill_numbers = [legis_num] if legis_num else []

            # Get document URLs for source linking
            doc_url = None
            for f in item.findall(".//file"):
                doc_url = f.get("doc-url")
                break

            event = ScheduleEvent(
                title=title,
                date=item_date,
                time=None,  # Floor items don't have specific times
                chamber="House",
                event_type="floor",
                description=f"Category: {category_type}",
                bill_numbers=bill_numbers,
                source_url=doc_url or "https://docs.house.gov/floor/",
                source_name="House Clerk",
                event_id=item.get("id"),
            )
            events.append(event)

    logger.info(f"Fetched {len(events)} House floor items")
    return events


def fetch_senate_hearings_xml() -> list[ScheduleEvent]:
    """Fetch Senate committee hearings from senate.gov XML feed."""
    events = []
    url = "https://www.senate.gov/general/committee_schedules/hearings.xml"
    logger.info(f"Fetching Senate hearings XML: {url}")

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch Senate hearings XML: {e}")
        return events

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        logger.error(f"Failed to parse Senate hearings XML: {e}")
        return events

    today = date.today()
    week_end = today + timedelta(days=(6 - today.weekday()))  # Sunday

    for meeting in root.findall("meeting"):
        date_str = meeting.findtext("date_iso_8601", "").strip()
        if not date_str:
            continue

        try:
            meeting_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        # Only include this week's meetings
        monday = get_current_week_monday()
        if meeting_date < monday or meeting_date > monday + timedelta(days=6):
            continue

        committee = meeting.findtext("committee", "").strip()
        subcommittee = meeting.findtext("sub_cmte", "").strip() or None
        time_str = meeting.findtext("time", "").strip() or None
        room = meeting.findtext("room", "").strip() or None
        matter = meeting.findtext("matter", "").strip() or None

        # Extract bill numbers from associated documents
        bill_numbers = []
        docs_elem = meeting.find("Documents")
        if docs_elem is not None:
            for doc in docs_elem.findall("AssociatedDocument"):
                prefix = doc.get("document_prefix", "")
                num = doc.get("document_num", "")
                if prefix and num:
                    # Convert prefix: SN -> S., HR -> H.R.
                    if prefix == "SN":
                        bill_numbers.append(f"S. {num}")
                    elif prefix == "HR":
                        bill_numbers.append(f"H.R. {num}")
                    elif prefix == "PN":
                        bill_numbers.append(f"PN {num}")
                    else:
                        bill_numbers.append(f"{prefix} {num}")

        # Build title
        display_committee = committee
        if subcommittee:
            display_committee = f"{committee} - {subcommittee}"

        # Truncate matter for title if it's very long
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
    # Truncate very long titles (some API entries have full bill text as title)
    if len(title) > 200:
        title = title[:197] + "..."
    meeting_type = detail.get("type", "Meeting")
    status = detail.get("meetingStatus", "")

    if status.lower() in ("cancelled", "postponed"):
        return None

    committees = detail.get("committees", [])
    committee_name = committees[0].get("name", "") if committees else ""

    location_data = detail.get("location", {})
    location = None
    if location_data:
        building = location_data.get("building", "")
        room = location_data.get("room", "")
        location = f"{building}, Room {room}" if building and room else building or room

    time_str = None
    if meeting_dt.hour > 0:
        et_hour = meeting_dt.hour - 4  # EDT approximation
        if et_hour < 0:
            et_hour += 24
        am_pm = "AM" if et_hour < 12 else "PM"
        display_hour = et_hour if et_hour <= 12 else et_hour - 12
        if display_hour == 0:
            display_hour = 12
        time_str = f"{display_hour}:{meeting_dt.minute:02d} {am_pm}"

    event_type = "hearing"
    if "markup" in meeting_type.lower():
        event_type = "markup"
    elif "meeting" in meeting_type.lower():
        event_type = "meeting"

    event_id = detail.get("eventId", "")
    congress_url = (
        f"https://www.congress.gov/event/119th-congress/{chamber.lower()}-event/{event_id}"
        if event_id else None
    )

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


def fetch_congress_api_meetings() -> list[ScheduleEvent]:
    """Fetch committee meetings from Congress.gov API using parallel requests."""
    events = []
    monday = get_current_week_monday()
    sunday = monday + timedelta(days=6)

    # Fetch the most recently updated meetings (top 50 should cover current week)
    url = (
        f"https://api.congress.gov/v3/committee-meeting"
        f"?api_key={CONGRESS_API_KEY}"
        f"&limit=50&format=json"
    )
    logger.info("Fetching Congress.gov API meeting list")

    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.error(f"Failed to fetch Congress.gov API: {e}")
        return events

    meetings = data.get("committeeMeetings", [])
    detail_urls = [m.get("url") for m in meetings if m.get("url")]

    # Fetch details in parallel (max 10 concurrent)
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

    logger.info(f"Fetched {len(events)} Congress.gov API events (from {len(details)} details)")
    return events


def _dedup_key(event: ScheduleEvent) -> str:
    """Generate a dedup key for an event. Uses committee+date+time for committee events,
    or title-based key for floor items."""
    if event.event_type == "floor":
        # Floor items: dedup on bill number + date
        bill = event.bill_numbers[0] if event.bill_numbers else event.title[:40]
        return f"floor|{event.date}|{bill.lower().strip()}"

    # Committee events: match on date + time + committee name
    committee_norm = re.sub(r'\s+', ' ', (event.committee or event.title[:40]).lower().strip())
    # Remove common prefixes like "house " or "senate "
    committee_norm = re.sub(r'^(house|senate)\s+', '', committee_norm)
    # Normalize time: "04:00 PM" and "4:00 PM" should match
    time_raw = (event.time or "").strip()
    try:
        t = datetime.strptime(time_raw, "%I:%M %p")
        time_norm = t.strftime("%H:%M")
    except ValueError:
        try:
            t = datetime.strptime(time_raw, "%I:%M%p")
            time_norm = t.strftime("%H:%M")
        except ValueError:
            time_norm = time_raw.lower()
    return f"committee|{event.date}|{time_norm}|{committee_norm[:50]}"


def fetch_all_events() -> list[ScheduleEvent]:
    """Fetch events from all sources and return sorted, deduplicated list."""
    all_events = []

    # Fetch from all sources
    all_events.extend(fetch_house_floor_xml())
    all_events.extend(fetch_senate_hearings_xml())
    all_events.extend(fetch_congress_api_meetings())

    # Deduplicate: prefer Senate.gov for Senate hearings (better truncated titles),
    # prefer Congress.gov for House meetings (has location data)
    # Sort so preferred sources come first
    def source_priority(e):
        if e.chamber == "Senate" and e.source_name == "Senate.gov":
            return 0  # Prefer Senate.gov for Senate events
        if e.source_name == "Congress.gov":
            return 1
        return 2

    seen = set()
    deduped = []
    for event in sorted(all_events, key=source_priority):
        key = _dedup_key(event)
        if key not in seen:
            seen.add(key)
            deduped.append(event)

    deduped.sort(key=lambda e: e.sort_key)
    logger.info(f"Total events after dedup: {len(deduped)}")
    return deduped


def get_week_range():
    """Return (monday, sunday) for the current week."""
    monday = get_current_week_monday()
    sunday = monday + timedelta(days=6)
    return monday, sunday
