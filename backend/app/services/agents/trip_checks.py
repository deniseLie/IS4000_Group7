"""Deterministic trip checks (build plan phase 7), in the retail_checks style: the trip window, the card and loan due
dates inside it, and the shift of a due date that falls on a weekend (public holidays are not checked).

Code computes every date here so the model never calculates one. A window that cannot be read is a clarifying
question, never a guess. Dates come from the planner's from_date/to_date entities or the customer's own words.
"""
import re
from datetime import date, timedelta
from app.services.agents.retail_checks import MONTHS

MAX_DAYS = 180  # one trip; a longer window is asked again
ASK_WINDOW = 'Which dates will you be away? For example “10 to 20 November”.'
DAY_MONTH = re.compile(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\b', re.I)
RANGE = re.compile(r'\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:-|–|to|until|till)\s*(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\b', re.I)


def as_date(value: object, today: date) -> date | None:
    """An ISO date, or a day and month (this year's date unless that is more than half a year ago)."""
    if not isinstance(value, str):return None
    text = value.strip().lower()
    if found := re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', text):
        try:return date(int(found[1]), int(found[2]), int(found[3]))
        except ValueError:return None
    if (found := DAY_MONTH.fullmatch(text)) and found[2] in MONTHS:
        try:value = date(today.year, MONTHS[found[2]], int(found[1]))
        except ValueError:return None
        return value if (today-value).days <= 183 else value.replace(year=today.year+1)
    return None


def window(entities: dict, question: str, today: date) -> tuple[date, date] | None:
    """The trip, from entities first, else a "10 to 20 November" range in her words. None when it is missing, reversed,
    already over, or longer than MAX_DAYS."""
    start, end = as_date(entities.get('from_date'), today), as_date(entities.get('to_date'), today)
    if (start is None or end is None) and (found := RANGE.search(question)) and found[3].lower() in MONTHS:
        start, end = as_date(f'{found[1]} {found[3]}', today), as_date(f'{found[2]} {found[3]}', today)
    if start is None or end is None or end < start or end < today or (end-start).days > MAX_DAYS:return None
    return start, end


def add_month(day: date) -> date:
    year, month = (day.year+1, 1) if day.month == 12 else (day.year, day.month+1)
    for last in (31, 30, 29, 28):
        try:return date(year, month, min(day.day, last))
        except ValueError:continue
    raise ValueError(day)


def pays_on(day: date) -> tuple[date, str | None]:
    """A due date on a Saturday or Sunday is paid the next Monday."""
    if day.weekday() < 5:return day, None
    return day + timedelta(days=7-day.weekday()), 'weekend'


def dues(items: list[tuple[str, date]], start: date, end: date) -> list[dict]:
    """Each monthly due date from its next recorded one to the end of the trip, with the day it is actually paid."""
    found = []
    for label, due in items:
        while due <= end + timedelta(days=7):  # a due just before the end may be paid inside the trip after a shift
            paid, why = pays_on(due)
            if paid >= start and due >= start - timedelta(days=7):
                found.append({'what': label, 'due': due.isoformat(), 'pays_on': paid.isoformat(), 'shifted_for': why, 'in_trip': start <= paid <= end})
            due = add_month(due)
    return sorted(found, key=lambda d: (d['pays_on'], d['what']))
