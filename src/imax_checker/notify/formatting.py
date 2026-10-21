"""Shared text / HTML rendering for notifications."""

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from datetime import date
from html import escape
from itertools import groupby

from imax_checker.models import RunContext, Screening

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def fmt_date(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def fmt_day_heading(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {fmt_date(day)}"


def fmt_time(screening: Screening) -> str:
    return screening.starts_at.strftime("%H:%M")


def fmt_availability(ratio: float | None) -> str:
    """Nearest whole percent of seats still available. Empty when the API omitted the ratio."""
    if ratio is None:
        return ""
    percent = min(100, max(0, round(ratio * 100)))
    return f"{percent}% available"


def is_followup(screenings: Sequence[Screening], new_ids: AbstractSet[str]) -> bool:
    """A follow-up announces new screenings next to ones that were already announced."""
    return any(s.event_id not in new_ids for s in screenings)


def group_by_date(screenings: Sequence[Screening]) -> list[tuple[date, list[Screening]]]:
    """Group by the real calendar date of the start (after-midnight shows get the next date)."""
    ordered = sorted(screenings, key=lambda s: (s.starts_at, s.event_id))
    return [(day, list(group)) for day, group in groupby(ordered, key=lambda s: s.starts_at.date())]


def render_title(context: RunContext, *, followup: bool = False) -> str:
    prefix = "New IMAX screenings on sale" if followup else "IMAX tickets on sale"
    return f"{prefix}: {context.movie} @ {context.cinema_name}"


def render_subject(screenings: Sequence[Screening], context: RunContext, *, followup: bool) -> str:
    ordered = sorted(screenings, key=lambda s: s.starts_at)
    first, last = fmt_date(ordered[0].starts_at.date()), fmt_date(ordered[-1].starts_at.date())
    span = first if first == last else f"{first}–{last}"
    count = len(ordered)
    noun = "screening" if count == 1 else "screenings"
    subject = f"IMAX tickets on sale: {context.movie} – {count} {noun} {span}"
    return f"NEW: {subject}" if followup else subject


def render_footer(context: RunContext) -> str:
    return (
        f"Checked range: {fmt_date(context.date_from)} – {fmt_date(context.date_to)}"
        f" · cinema {context.cinema_id}"
    )


def render_text(
    screenings: Sequence[Screening], new_ids: AbstractSet[str], context: RunContext
) -> str:
    followup = is_followup(screenings, new_ids)
    width = max((len(s.auditorium) for s in screenings), default=0)
    labels = {s.event_id: fmt_availability(s.availability_ratio) for s in screenings}
    avail_width = max((len(label) for label in labels.values()), default=0)
    lines = [render_title(context, followup=followup), ""]
    for day, group in group_by_date(screenings):
        lines.append(fmt_day_heading(day))
        for s in group:
            avail = f"{labels[s.event_id]:<{avail_width}}  " if avail_width else ""
            tag = "NEW  " if followup and s.event_id in new_ids else ""
            lines.append(f"  {fmt_time(s)}  {s.auditorium:<{width}}  {avail}{tag}{s.booking_url}")
    lines += ["", render_footer(context)]
    return "\n".join(lines)


def render_html(
    screenings: Sequence[Screening], new_ids: AbstractSet[str], context: RunContext
) -> str:
    followup = is_followup(screenings, new_ids)
    parts = [
        "<html><body>",
        f"<h2>{escape(render_title(context, followup=followup))}</h2>",
    ]
    for day, group in group_by_date(screenings):
        parts.append(f"<h3>{escape(fmt_day_heading(day))}</h3><ul>")
        for s in group:
            tag = " <strong>NEW</strong>" if followup and s.event_id in new_ids else ""
            label = fmt_availability(s.availability_ratio)
            avail = f" &middot; {escape(label)}" if label else ""
            url = escape(s.booking_url, quote=True)
            parts.append(
                f"<li>{fmt_time(s)} &middot; {escape(s.auditorium)}{tag}{avail} &ndash; "
                f'<a href="{url}">Book tickets</a></li>'
            )
        parts.append("</ul>")
    parts.append(f"<p><small>{escape(render_footer(context))}</small></p>")
    parts.append("</body></html>")
    return "\n".join(parts)
