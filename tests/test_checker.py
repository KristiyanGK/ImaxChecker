from datetime import date, datetime

import pytest

from imax_checker.checker import CheckResult, check, date_range, is_imax
from imax_checker.cinemacity import PRAGUE, CinemaCityError, Event

from .conftest import (
    NOW,
    TODAY,
    FakeClient,
    fixture_response,
    load_fixture,
    make_body,
    make_event,
    make_settings,
)

D27, D28, D29, D30 = (date(2026, 12, d) for d in (27, 28, 29, 30))


def run(days, **settings):
    settings.setdefault("date_from", D27)
    settings.setdefault("date_to", D30)
    client = FakeClient(days)
    return check(make_settings(**settings), client, today=TODAY, now=NOW), client


def event_model(**kwargs) -> Event:
    return Event.model_validate(make_event(**kwargs))


# --- real fixture -----------------------------------------------------------------------------


def test_real_fixture_returns_only_imax_dune_screenings_sorted():
    body = load_fixture("film_events_flora_2026-12-18.json")["body"]
    day = date(2026, 12, 18)
    result, _ = run({day: body}, date_from=day, date_to=day)

    assert result.on_sale
    assert [s.starts_at.strftime("%H:%M") for s in result.bookable] == [
        "10:40",
        "14:00",
        "17:20",
        "20:40",
    ]
    assert {s.film_name for s in result.bookable} == {"Dune: Part Three"}
    assert {s.auditorium for s in result.bookable} == {"IMAX VOLVO"}
    assert all(s.starts_at.tzinfo is not None for s in result.bookable)
    assert result.bookable[0].starts_at.utcoffset().total_seconds() == 3600  # CET in December
    assert result.bookable == sorted(result.bookable, key=lambda s: s.starts_at)
    # The 18:00 Sál 01 screening is counted but never returned.
    assert result.non_imax_listed == 1
    assert all(s.auditorium != "Sál 01" for s in result.bookable)
    assert (
        result.bookable[0].booking_url
        == "https://www.cinemacity.cz/cz/booking-router/launch/1052/267906?lang=en"
    )
    assert [s.availability_ratio for s in result.bookable] == [0.3195, 0.2494, 0.2026, 0.2]
    assert fixture_response("film_events_flora_2026-12-18.json").events  # sanity


def test_name_normalization_in_check():
    body = make_body([make_event("1")])
    for movie in ("dune part three", "DUNE: PART THREE"):
        result, _ = run({D27: body}, movie=movie)
        assert result.on_sale


def test_other_films_are_ignored():
    body = make_body([make_event("1")], film_name="Avengers: Doomsday")
    result, _ = run({D27: body})
    assert not result.on_sale
    assert result.non_imax_listed == 0


# --- bookable rules ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"block": True},
        {"block": True, "block_until": "2026-12-20T10:00:00"},  # still in the future
        {"link": None},
        {"link": "", "composite": False},
    ],
)
def test_not_bookable(overrides):
    result, _ = run({D27: make_body([make_event("1", **overrides)])})
    assert not result.on_sale


def test_blocked_until_in_the_past_is_bookable():
    event = make_event("1", block=True, block_until="2026-10-01T10:00:00")
    result, _ = run({D27: make_body([event])})
    assert result.on_sale


def test_notification_link_is_the_site_when_launch_link_is_missing():
    event = make_event("1", link=None, cinemaId="")
    event["compositeBookingLink"]["bookingUrl"] = {"url": "https://example.com/book/1"}
    result, _ = run({D27: make_body([event])})
    assert (
        result.bookable[0].booking_url
        == "https://www.cinemacity.cz/cz/booking-router/launch/1052/1?lang=en"
    )


def test_api_booking_url_is_kept_when_the_site_link_cannot_be_built():
    event = make_event("1", link=None, cinemaId="")
    event["compositeBookingLink"]["bookingUrl"] = {"url": "https://example.com/book/1"}
    result, _ = run({D27: make_body([event])}, cinema_id="")
    assert result.bookable[0].booking_url == "https://example.com/book/1"


def test_seat_occupancy_is_ignored():
    events = [
        make_event("1", soldOut=True),
        make_event("2", start="2026-12-27T20:40:00", availabilityRatio=0),
    ]
    result, _ = run({D27: make_body(events)})
    assert [s.event_id for s in result.bookable] == ["1", "2"]
    assert [s.availability_ratio for s in result.bookable] == [0.5, 0]


def test_on_sale_with_one_bookable_screening_in_range():
    events = [make_event("1", link=None), make_event("2", start="2026-12-27T20:40:00")]
    result, _ = run({D27: make_body(events)})
    assert result.on_sale
    assert [s.event_id for s in result.bookable] == ["2"]


def test_empty_days_give_no_results_and_no_error():
    result, client = run({})
    assert result == CheckResult(
        bookable=[], non_imax_listed=0, checked_days=[D27, D28, D29, D30], failed_days=[]
    )
    assert not result.on_sale
    assert client.requested == [D27, D28, D29, D30]


def test_after_midnight_screening_keeps_real_datetime_and_business_day():
    event = make_event("1", start="2026-12-31T00:01:00", business_day="2026-12-30")
    result, _ = run({D30: make_body([event])})
    screening = result.bookable[0]
    assert screening.business_day == D30
    assert screening.starts_at == datetime(2026, 12, 31, 0, 1, tzinfo=PRAGUE)


# --- IMAX detection ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"auditorium": "IMAX VOLVO", "tiny": ""}, True),
        ({"auditorium": "Sál 03", "tiny": "IMAX"}, True),
        ({"auditorium": "imax hall", "tiny": ""}, True),
        ({"auditorium": "Sál 01", "tiny": "1", "attrs": ["2d", "imax"]}, True),
        ({"auditorium": "Sál 01", "tiny": "1", "attrs": ["2d"]}, False),
    ],
)
def test_is_imax(kwargs, expected):
    assert is_imax(event_model(**kwargs)) is expected


def test_movie_only_in_non_imax_halls_is_not_on_sale(caplog):
    event = make_event("1", auditorium="Sál 01", tiny="1")
    with caplog.at_level("INFO"):
        result, _ = run({D27: make_body([event])})
    assert not result.on_sale
    assert result.non_imax_listed == 1
    assert "27.12.2026 in Sál 01 only, no IMAX yet" in caplog.text
    assert "NOT on sale yet (1 non-IMAX screenings listed)" in caplog.text


def test_mixed_halls_only_imax_returned():
    events = [
        make_event("1", auditorium="Sál 01", tiny="1"),
        make_event("2", start="2026-12-27T20:40:00"),
    ]
    result, _ = run({D27: make_body(events)})
    assert [s.event_id for s in result.bookable] == ["2"]
    assert result.non_imax_listed == 1


# --- failures, dates ---------------------------------------------------------------------------


def test_failed_day_does_not_stop_other_days():
    days = {
        D27: CinemaCityError("HTTP 500"),
        D28: make_body([make_event("1", start="2026-12-28T17:20:00")]),
    }
    result, _ = run(days)
    assert result.failed_days == [D27]
    assert result.checked_days == [D28, D29, D30]
    assert [s.event_id for s in result.bookable] == ["1"]


def test_past_days_are_skipped():
    result, client = run(
        {D27: make_body([make_event("1")])},
        date_from=date(2026, 10, 20),
        date_to=date(2026, 10, 22),
    )
    assert client.requested == [date(2026, 10, 21), date(2026, 10, 22)]
    assert result.checked_days == [date(2026, 10, 21), date(2026, 10, 22)]


def test_date_range_is_inclusive():
    assert date_range(D27, D30) == [D27, D28, D29, D30]
    assert date_range(D27, D27) == [D27]
