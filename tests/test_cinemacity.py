from datetime import date

import httpx
import pytest
import respx
from tenacity import wait_none

from imax_checker.cinemacity import (
    BASE_URL,
    PRAGUE,
    CinemaCityClient,
    CinemaCityError,
    Film,
    FilmEventsResponse,
    match_films,
    normalize,
)

from .conftest import load_fixture

DAY = date(2026, 12, 18)
URL = f"{BASE_URL}/film-events/in-cinema/1052/at-date/2026-12-18"


@pytest.fixture
def client():
    with CinemaCityClient(wait=wait_none()) as c:
        yield c


@respx.mock
def test_parses_real_fixture(client):
    route = respx.get(URL).respond(json=load_fixture("film_events_flora_2026-12-18.json"))
    response = client.film_events("1052", DAY)

    assert {f.name for f in response.films} >= {"Dune: Part Three"}
    dune = next(e for e in response.events if e.film_id == "8105s2r" and e.auditorium == "Sál 01")
    assert dune.event_date_time.tzinfo is not None
    assert dune.event_date_time.utcoffset() == PRAGUE.utcoffset(
        dune.event_date_time.replace(tzinfo=None)
    )
    assert dune.business_day == DAY
    assert dune.availability_ratio == 0.9932
    request = route.calls.last.request
    assert request.url.params["lang"] == "en_GB"
    assert request.url.params["attr"] == ""
    assert "imax-checker" in request.headers["user-agent"]


@respx.mock
def test_parses_empty_fixture(client):
    respx.get(URL).respond(json=load_fixture("film_events_empty.json"))
    response = client.film_events("1052", DAY)
    assert response.films == []
    assert response.events == []


def test_ignores_unknown_fields():
    response = FilmEventsResponse.model_validate(
        {
            "films": [{"id": "f", "name": "X", "somethingNew": 1}],
            "events": [
                {
                    "id": "1",
                    "filmId": "f",
                    "businessDay": "2026-12-27",
                    "eventDateTime": "2026-12-27T10:00:00",
                    "brandNewField": {"a": 1},
                }
            ],
            "alsoNew": [],
        }
    )
    assert response.events[0].auditorium == ""
    assert response.events[0].availability_ratio is None


@respx.mock
def test_retries_on_503_then_succeeds(client):
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(429),
            httpx.Response(200, json=load_fixture("film_events_empty.json")),
        ]
    )
    assert client.film_events("1052", DAY).events == []
    assert route.call_count == 3


@respx.mock
def test_retries_on_transport_error(client):
    route = respx.get(URL).mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json=load_fixture("film_events_empty.json")),
        ]
    )
    client.film_events("1052", DAY)
    assert route.call_count == 2


@respx.mock
def test_gives_up_after_three_attempts(client):
    route = respx.get(URL).respond(500)
    with pytest.raises(CinemaCityError):
        client.film_events("1052", DAY)
    assert route.call_count == 3


@respx.mock
def test_404_raises_without_retry(client):
    route = respx.get(URL).respond(404)
    with pytest.raises(CinemaCityError, match="404"):
        client.film_events("1052", DAY)
    assert route.call_count == 1


@respx.mock
def test_unexpected_shape_raises(client):
    respx.get(URL).respond(json={"nope": 1})
    with pytest.raises(CinemaCityError):
        client.film_events("1052", DAY)


@respx.mock
def test_films_endpoint(client):
    respx.get(f"{BASE_URL}/films/until/2026-12-30").respond(
        json={"body": {"films": [{"id": "8105s2r", "name": "Dune: Part Three"}]}}
    )
    assert [f.id for f in client.films(date(2026, 12, 30))] == ["8105s2r"]


@pytest.mark.parametrize("name", ["dune part three", "DUNE: PART THREE", "Dune – Part  Three!"])
def test_normalize_matches(name):
    assert normalize(name) == normalize("Dune: Part Three")


def test_normalize_strips_accents():
    assert normalize("Čaj  Šálek") == "caj salek"


def test_match_films_prefers_exact_and_falls_back_to_substring(caplog):
    films = [Film(id="1", name="Dune: Part Three"), Film(id="2", name="Dune: Part Three Making Of")]
    assert [f.id for f in match_films(films, "dune part three")] == ["1"]

    with caplog.at_level("INFO"):
        fuzzy = match_films(films, "Part Three Making")
    assert [f.id for f in fuzzy] == ["2"]
    assert "Dune: Part Three Making Of" in caplog.text

    assert match_films(films, "Something else") == []
    assert match_films(films, "") == []
