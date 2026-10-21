import logging
import re
import unicodedata
from collections.abc import Callable
from datetime import date, datetime
from types import TracebackType
from typing import Self
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

log = logging.getLogger(__name__)

PRAGUE = ZoneInfo("Europe/Prague")
BASE_URL = "https://www.cinemacity.cz/cz/data-api-service/v1/quickbook/10101"
USER_AGENT = "imax-checker/0.1 (+kristiangk98@gmail.com)"


class CinemaCityError(Exception):
    """The Cinema City API returned an unusable response."""


class _RetryableError(Exception):
    """Internal: network error, 5xx or 429. Triggers a retry."""


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class Film(ApiModel):
    id: str
    name: str
    attribute_ids: list[str] = Field(default_factory=list)


class BookingUrl(ApiModel):
    url: str | None = None


class BookingInfo(ApiModel):
    block_online_sales: bool = False
    block_online_sales_until: datetime | None = None
    booking_url: BookingUrl | None = None

    @field_validator("block_online_sales", mode="before")
    @classmethod
    def _none_is_not_blocked(cls, value: object) -> object:
        return False if value is None else value

    @field_validator("block_online_sales_until")
    @classmethod
    def _localize(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=PRAGUE)
        return value


class Event(ApiModel):
    id: str
    film_id: str
    cinema_id: str = ""
    business_day: date
    event_date_time: datetime  # Prague local time, made tz-aware below
    auditorium: str = ""
    auditorium_tiny_name: str = ""
    booking_link: str | None = None
    booking_router_launch_link: str | None = None
    composite_booking_link: BookingInfo | None = None
    attribute_ids: list[str] = Field(default_factory=list)
    availability_ratio: float | None = None  # fraction of seats still available; not a count

    @field_validator("event_date_time")
    @classmethod
    def _localize(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=PRAGUE)
        return value.astimezone(PRAGUE)

    @field_validator("auditorium", "auditorium_tiny_name", "cinema_id", mode="before")
    @classmethod
    def _none_is_empty(cls, value: object) -> object:
        return "" if value is None else value


class FilmEventsResponse(ApiModel):
    films: list[Film] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)


def normalize(name: str) -> str:
    """Casefold, strip accents, collapse every non-alphanumeric run into one space."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", " ", stripped).strip()


def match_films(films: list[Film], movie: str) -> list[Film]:
    """Exact normalized name match, falling back to a substring match (logged)."""
    wanted = normalize(movie)
    if not wanted:
        return []
    exact = [film for film in films if normalize(film.name) == wanted]
    if exact:
        return exact
    fuzzy = [film for film in films if wanted in normalize(film.name)]
    if fuzzy:
        log.info(
            "No exact match for %r, using substring matches: %s",
            movie,
            ", ".join(repr(film.name) for film in fuzzy),
        )
    return fuzzy


def _default_wait() -> wait_base:
    return wait_exponential_jitter(initial=1, max=30)


class CinemaCityClient:
    def __init__(
        self,
        *,
        lang: str = "en_GB",
        timeout: float = 15.0,
        base_url: str = BASE_URL,
        wait: wait_base | Callable[..., float] | None = None,
        attempts: int = 3,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._lang = lang
        self._wait = wait or _default_wait()
        self._attempts = attempts
        self._client = http_client or httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get_once(self, path: str) -> dict:
        try:
            response = self._client.get(path, params={"attr": "", "lang": self._lang})
        except httpx.TransportError as exc:
            raise _RetryableError(f"network error on {path}: {exc!r}") from exc
        status = response.status_code
        if status == 429 or status >= 500:
            raise _RetryableError(f"HTTP {status} on {path}")
        if status >= 400:
            raise CinemaCityError(f"HTTP {status} on {path}")
        try:
            return response.json()
        except ValueError as exc:
            raise CinemaCityError(f"invalid JSON on {path}") from exc

    def _get(self, path: str) -> dict:
        retrying = Retrying(
            stop=stop_after_attempt(self._attempts),
            wait=self._wait,
            retry=retry_if_exception_type(_RetryableError),
            reraise=True,
        )
        try:
            return retrying(self._get_once, path)
        except _RetryableError as exc:
            raise CinemaCityError(f"giving up after {self._attempts} attempts: {exc}") from exc

    def _body(self, path: str) -> dict:
        data = self._get(path)
        body = data.get("body") if isinstance(data, dict) else None
        if not isinstance(body, dict):
            raise CinemaCityError(f"unexpected response shape on {path}")
        return body

    def film_events(self, cinema_id: str, day: date) -> FilmEventsResponse:
        path = f"/film-events/in-cinema/{cinema_id}/at-date/{day.isoformat()}"
        return FilmEventsResponse.model_validate(self._body(path))

    def films(self, until: date) -> list[Film]:
        body = self._body(f"/films/until/{until.isoformat()}")
        return FilmEventsResponse.model_validate({"films": body.get("films", [])}).films
