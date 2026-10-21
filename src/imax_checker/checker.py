import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from imax_checker.cinemacity import (
    PRAGUE,
    CinemaCityClient,
    CinemaCityError,
    Event,
    Film,
    match_films,
)
from imax_checker.config import Settings
from imax_checker.models import Screening

log = logging.getLogger(__name__)


@dataclass(slots=True)
class CheckResult:
    bookable: list[Screening] = field(default_factory=list)  # IMAX only, sorted by start
    non_imax_listed: int = 0  # for logging only
    checked_days: list[date] = field(default_factory=list)
    failed_days: list[date] = field(default_factory=list)

    @property
    def on_sale(self) -> bool:
        return bool(self.bookable)


def prague_today() -> date:
    return datetime.now(PRAGUE).date()


def is_imax(event: Event) -> bool:
    """The auditorium name is the primary signal; the `imax` attribute is a safety net."""
    if "imax" in event.auditorium.casefold() or "imax" in event.auditorium_tiny_name.casefold():
        return True
    return any(attr.casefold() == "imax" for attr in event.attribute_ids)


# bookingLink is POST-only (`/api/order/...`, a browser GET is a 404). This is the page
# the cinema site itself opens so a person can buy the ticket.
_LAUNCH_URL = "https://www.cinemacity.cz/cz/booking-router/launch/{cinema_id}/{event_id}?lang=en"


def api_booking_url(event: Event) -> str:
    """Machine link that means online sales exist. Not something to put in a notification."""
    if event.booking_link:
        return event.booking_link
    composite = event.composite_booking_link
    if composite and composite.booking_url and composite.booking_url.url:
        return composite.booking_url.url
    return ""


def site_booking_url(event: Event, cinema_id: str) -> str:
    """Page a person can open to buy this screening."""
    if event.booking_router_launch_link:
        return event.booking_router_launch_link
    cid = event.cinema_id or cinema_id
    if cid and event.id:
        return _LAUNCH_URL.format(cinema_id=cid, event_id=event.id)
    return api_booking_url(event)


def is_bookable(event: Event, now: datetime) -> bool:
    """Has a booking link and online sales are not (still) blocked.

    Seat occupancy is not part of this decision. ``availability_ratio`` is copied onto the
    screening so notifications can show it.
    """
    if not api_booking_url(event):
        return False
    composite = event.composite_booking_link
    if composite is None or not composite.block_online_sales:
        return True
    until = composite.block_online_sales_until
    return until is not None and until <= now


def _fmt(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def date_range(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=n) for n in range((date_to - date_from).days + 1)]


def _to_screening(event: Event, film: Film, cinema_id: str) -> Screening:
    return Screening(
        event_id=event.id,
        film_name=film.name,
        starts_at=event.event_date_time,
        business_day=event.business_day,
        auditorium=event.auditorium or event.auditorium_tiny_name,
        booking_url=site_booking_url(event, cinema_id),
        availability_ratio=event.availability_ratio,
    )


def check(
    settings: Settings,
    client: CinemaCityClient,
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> CheckResult:
    today = today or prague_today()
    now = now or datetime.now(PRAGUE)
    result = CheckResult()
    bookable: dict[str, Screening] = {}

    for day in date_range(settings.date_from, settings.date_to):
        if day < today:
            log.debug("Skipping past day %s", _fmt(day))
            continue
        try:
            response = client.film_events(settings.cinema_id, day)
        except CinemaCityError as exc:
            log.warning("Fetching %s failed: %s", _fmt(day), exc)
            result.failed_days.append(day)
            continue
        result.checked_days.append(day)

        films = {film.id: film for film in match_films(response.films, settings.movie)}
        imax_events = 0
        non_imax_halls: set[str] = set()
        for event in response.events:
            film = films.get(event.film_id)
            if film is None:
                continue
            if not is_imax(event):
                result.non_imax_listed += 1
                non_imax_halls.add(event.auditorium or event.auditorium_tiny_name or "?")
                continue
            imax_events += 1
            if is_bookable(event, now):
                bookable[event.id] = _to_screening(event, film, settings.cinema_id)
        if non_imax_halls and not imax_events:
            log.info(
                "%s listed on %s in %s only, no IMAX yet",
                settings.movie,
                _fmt(day),
                ", ".join(sorted(non_imax_halls)),
            )

    result.bookable = sorted(bookable.values(), key=lambda s: (s.starts_at, s.event_id))

    days = len(result.checked_days)
    checked = f"checked {days} {'day' if days == 1 else 'days'}"
    if result.failed_days:
        checked += f" ({len(result.failed_days)} failed)"
    if result.on_sale:
        log.info("%s: IMAX tickets ON SALE (%d screenings)", checked, len(result.bookable))
    else:
        log.info(
            "%s: IMAX tickets NOT on sale yet (%d non-IMAX screenings listed)",
            checked,
            result.non_imax_listed,
        )
    return result
