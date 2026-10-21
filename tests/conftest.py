import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from imax_checker.cinemacity import PRAGUE, FilmEventsResponse
from imax_checker.config import Settings
from imax_checker.models import RunContext, Screening

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=PRAGUE)


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_response(name: str) -> FilmEventsResponse:
    return FilmEventsResponse.model_validate(load_fixture(name)["body"])


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.startswith("IMAX_"):
            monkeypatch.delenv(key)


def make_settings(**overrides: Any) -> Settings:
    """Settings that ignore sys.argv (pytest's), `.env` and the environment."""
    return Settings(_cli_parse_args=[], _env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


def make_event(
    event_id: str = "1",
    *,
    film_id: str = "8105s2r",
    start: str = "2026-12-27T17:20:00",
    business_day: str | None = None,
    auditorium: str = "IMAX VOLVO",
    tiny: str = "IMAX",
    attrs: list[str] | None = None,
    link: str | None = "default",
    block: bool = False,
    block_until: str | None = None,
    composite: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": event_id,
        "filmId": film_id,
        "cinemaId": "1052",
        "businessDay": business_day or start[:10],
        "eventDateTime": start,
        "attributeIds": attrs if attrs is not None else ["2d"],
        "bookingLink": (
            f"https://tickets.cinemacity.cz/api/order/{event_id}?lang=en"
            if link == "default"
            else link
        ),
        "auditorium": auditorium,
        "auditoriumTinyName": tiny,
        "soldOut": False,
        "availabilityRatio": 0.5,
        **extra,
    }
    if composite:
        event["compositeBookingLink"] = {
            "blockOnlineSales": block,
            "blockOnlineSalesUntil": block_until,
        }
    return event


def make_body(events: list[dict[str, Any]], film_name: str = "Dune: Part Three") -> dict[str, Any]:
    films = [{"id": "8105s2r", "name": film_name, "attributeIds": []}] if events else []
    return {"films": films, "events": events}


class FakeClient:
    """Stands in for CinemaCityClient. `days` maps a date to a body dict or an exception."""

    def __init__(self, days: dict[date, dict[str, Any] | Exception]) -> None:
        self.days = days
        self.requested: list[date] = []

    def film_events(self, cinema_id: str, day: date) -> FilmEventsResponse:
        self.requested.append(day)
        value = self.days.get(day, {"films": [], "events": []})
        if isinstance(value, Exception):
            raise value
        return FilmEventsResponse.model_validate(value)


def make_screening(
    event_id: str = "1",
    start: str = "2026-12-27T17:20:00",
    auditorium: str = "IMAX VOLVO",
    availability_ratio: float | None = 0.5,
) -> Screening:
    starts_at = datetime.fromisoformat(start).replace(tzinfo=PRAGUE)
    return Screening(
        event_id=event_id,
        film_name="Dune: Part Three",
        starts_at=starts_at,
        business_day=starts_at.date(),
        auditorium=auditorium,
        booking_url=(f"https://www.cinemacity.cz/cz/booking-router/launch/1052/{event_id}?lang=en"),
        availability_ratio=availability_ratio,
    )


@pytest.fixture
def context() -> RunContext:
    return RunContext(
        movie="Dune: Part Three",
        cinema_id="1052",
        date_from=date(2026, 12, 27),
        date_to=date(2026, 12, 30),
    )
