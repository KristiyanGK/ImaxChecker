from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class Screening:
    event_id: str
    film_name: str
    starts_at: datetime  # tz-aware, Europe/Prague
    business_day: date
    auditorium: str
    booking_url: str
    availability_ratio: float | None = None  # fraction of seats still available; display only


@dataclass(frozen=True, slots=True)
class RunContext:
    """What a notification needs to know about the run besides the screenings."""

    movie: str
    cinema_id: str
    date_from: date
    date_to: date
    cinema_name: str = "Cinema City Flora"
