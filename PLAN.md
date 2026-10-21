# Plan: Cinema City Flora IMAX "tickets on sale" checker

Implementation plan for an AI agent. Build a small Python 3.14 tool that, on a configurable interval (default: every 60 minutes), checks whether tickets can be bought for **at least one IMAX screening** of a given movie at **Cinema City – OC Flora (Prague, cinema id `1052`)** in a date range. When they can, it notifies via **email (multiple recipients)** and **Discord**, listing the date and time of every bookable IMAX screening in the range.

**Only IMAX screenings count.** Screenings of the same movie in regular halls (for example `Sál 01`) are ignored entirely: they never trigger a notification and never show up in one. This is a fixed rule, not a configurable filter.

The tool watches for ticket sales opening. Each listed screening includes how many seats are still free, as a percentage (`availabilityRatio`). That percentage never decides whether tickets are on sale, and `soldOut` is ignored. There is no exact free-seat count in this API.

Defaults: movie `Dune: Part Three`, dates `27.12.2026` – `30.12.2026` (inclusive).

---

## 1. Research result: public JSON API (no HTML scraping needed)

The booking page (`https://www.cinemacity.cz/cinemas/flora/1052?lang=en_GB#/buy-tickets-by-cinema?...`) is a client-side single-page app (note the `#/` hash route). Its data comes from an undocumented but public JSON API. Verified on 2026-10-21:

- No auth, cookies, API key or special headers are required. A default `httpx` client gets `200` (the API sits behind Cloudflare; still send a descriptive `User-Agent`).
- Responses are `application/json` with `cache-control: public, max-age=60`. Hourly polling is very light.
- **Decision:** use the JSON API. Do not use HTML/DOM scraping or a headless browser. Note in the README that if the API ever breaks, the fallback would be Playwright loading the SPA, since the DOM is rendered client-side. Do not implement that fallback now.

### Base URL

```
https://www.cinemacity.cz/cz/data-api-service/v1/quickbook/10101
```

`10101` is the tenant id for cinemacity.cz. Always pass the query parameters `attr=` and `lang=en_GB` so you get English film names.

### Endpoints

| Purpose | Path |
|---|---|
| Screenings for one cinema on one business day (**main endpoint**) | `/film-events/in-cinema/{cinema_id}/at-date/{YYYY-MM-DD}` |
| Dates with any schedule in a cinema | `/dates/in-cinema/{cinema_id}/until/{YYYY-MM-DD}` |
| All films (any cinema) | `/films/until/{YYYY-MM-DD}` |
| Cinemas list (ids and names) | `/cinemas/with-event/until/{YYYY-MM-DD}` |

Only `film-events` is needed for the core logic. The others are useful for debugging and the optional `--list-films` command.

### `film-events` response shape (trimmed; field names and the Dune values are real, the ids and length are illustrative)

```json
{
  "body": {
    "films": [
      {
        "id": "8105s2r",
        "name": "Dune: Part Three",
        "length": 165,
        "releaseDate": "2026-12-17T00:00:00",
        "attributeIds": ["12-plus", "4dx", "dolby-atmos", "original-lang-en", "vip", "..."]
      }
    ],
    "events": [
      {
        "id": "265001",
        "filmId": "8105s2r",
        "cinemaId": "1052",
        "businessDay": "2026-12-18",
        "eventDateTime": "2026-12-18T17:20:00",
        "attributeIds": ["12-plus", "2d", "..."],
        "bookingLink": "https://tickets.cinemacity.cz/api/order/265001?lang=en",
        "compositeBookingLink": {
          "blockOnlineSales": false,
          "blockOnlineSalesUntil": null
        },
        "bookingRouterLaunchLink": "https://www.cinemacity.cz/cz/booking-router/launch/1052/265001?lang=en",
        "soldOut": false,
        "availabilityRatio": 0.2026,
        "auditorium": "IMAX VOLVO",
        "auditoriumTinyName": "IMAX"
      }
    ]
  }
}
```

### Observed behaviour the implementation must handle

1. **Dates with no schedule yet return an empty body:** `{"body":{"films":[],"events":[]}}`. As of 2026-10-21, 27–30 December 2026 is empty at Flora. Dune: Part Three is listed with screenings on 15–20 December, so the 27–30 December screenings will show up later. That is exactly what the tool watches for. Empty is a normal state, not an error.
2. **`businessDay` vs `eventDateTime`:** late-night screenings belong to the previous business day. For example, `businessDay=2026-12-15` has an event at `2026-12-16T00:01:00`. Query by business day, but show the real `eventDateTime` in notifications.
3. **`eventDateTime` has no timezone.** It is Prague local time. Attach `ZoneInfo("Europe/Prague")`.
4. **IMAX is identified by the auditorium, not by attributes.** Flora has one IMAX hall: `auditorium = "IMAX VOLVO"`, `auditoriumTinyName = "IMAX"`. The `imax` attribute is **unreliable**. Verified on 2026-10-21: Street Fighter's IMAX events carry `imax` in `attributeIds`, but Dune: Part Three and The Odyssey in the same IMAX hall have only `["2d", ...]`, and neither film's `attributeIds` includes `imax`. The server-side filter `attr=imax` returns **0** Dune events, so **don't use `attr=imax`**. Always request `attr=` (empty) and filter on the client.
5. **`availabilityRatio`** is the fraction of seats still available (for example `0.3195` is about 32%). It is **not** a seat count, and the API has no hall capacity to multiply it by. Show it in every notification as a whole percent (`32% available`). Do **not** filter on it: a screening at `0` is still bookable. **`soldOut`** is ignored entirely: don't put it in the models and never filter on it. An exact free-seat count would require the ticket site's seat map (`tickets.cinemacity.cz`), which is a separate session-gated API. Don't call it.
6. **`filmId` is not a server-side filter.** The `dates` endpoint ignores it, so filter by film on the client.
7. Film names are localized by `lang`. Match on the English name with `lang=en_GB`.
8. **`bookingLink` is not a page a person can open.** It is `https://tickets.cinemacity.cz/api/order/{eventId}?lang=en`, and a browser GET returns 404 (the endpoint only accepts POST, which then redirects to the ticket app). The user-facing URL is `bookingRouterLaunchLink`: `https://www.cinemacity.cz/cz/booking-router/launch/{cinemaId}/{eventId}?lang=en`. Opening that page in a browser starts the purchase and lands on `https://tickets.cinemacity.cz/order/{eventId}`. Notifications must link to `bookingRouterLaunchLink`, not `bookingLink`. If the launch link is missing, build the same URL from `cinemaId` (or the configured cinema id) and the event id. Keep the API link only as proof that online sales exist.

### "Tickets can be bought" definition

A screening is **bookable** when all of these hold:

- the event's film matches the configured movie (normalized name match, see §4.3),
- it has a booking link (`bookingLink` or `compositeBookingLink.bookingUrl.url` is non-empty). That link is only the "sales exist" signal. The URL written into the notification is `bookingRouterLaunchLink` (see behaviour 8), so the recipient can open the cinema site and buy the ticket,
- online sales are not blocked: `compositeBookingLink.blockOnlineSales is False`, or `blockOnlineSalesUntil` is set and already in the past,
- **it is an IMAX screening** (always required): `"imax"` appears case-insensitively in `auditorium` **or** `auditoriumTinyName`, **or** `"imax"` is in the event's `attributeIds`. Implement this as `is_imax(event) -> bool` in `checker.py`. The attribute check is only a safety net; the auditorium name is the primary signal.

The **check result is a yes/no**: tickets are on sale if there is at least one bookable IMAX screening in the date range. When the answer is yes, the notification lists *all* bookable IMAX screenings in the range so the user can pick a slot.

When the movie is scheduled in the range but **only in non-IMAX halls**, the result is "not on sale". Log it at INFO level (for example `Dune: Part Three listed on 27.12 in Sál 01 only, no IMAX yet`) so it's clear the movie was found, but don't notify.

---

## 2. Tech stack

- **Python 3.14**, managed with **uv** (`uv` and `python3.14` are already installed). Set `requires-python = ">=3.14"`.
- Runtime dependencies:
  - `httpx`: HTTP client for the Cinema City API and Discord webhooks.
  - `pydantic` v2: typed models for API responses.
  - `pydantic-settings`: config from defaults, `.env`, environment variables and CLI arguments (use its built-in CLI parsing, so no separate argparse or typer).
  - `tenacity`: retries with exponential backoff on network errors and 5xx/429 responses.
- Email: the standard library's `smtplib` and `email.message.EmailMessage`, which is enough for a synchronous hourly job.
- Scheduling: a simple in-process loop plus a `--once` mode for cron or systemd timers. APScheduler isn't needed.
- Dev dependencies: `pytest`, `respx` (mocks httpx), `ruff`.
- Keep the code synchronous. At most about 4 requests per hour don't justify asyncio.
- Use modern Python 3.14 idioms: PEP 649 deferred annotations (no `from __future__ import annotations`), the `type X = ...` alias syntax, `datetime.UTC`, `zoneinfo`, and `pathlib`.

---

## 3. Project layout

The workspace currently contains only a PyCharm sample `main.py` and `.idea/`. It is not a git repository; run `git init`.

```
Imax_checker/
├── pyproject.toml
├── uv.lock
├── .gitignore               # .env, .state/, .venv/, __pycache__/, .idea/workspace.xml
├── .env.example             # every setting documented, no real secrets
├── README.md                # setup, config, run modes, cron/systemd examples
├── main.py                  # replace sample: `from imax_checker.cli import main; main()`
├── src/imax_checker/
│   ├── __init__.py
│   ├── __main__.py          # enables `python -m imax_checker`
│   ├── config.py            # Settings (pydantic-settings)
│   ├── cinemacity.py        # API client + response models
│   ├── checker.py           # movie matching + "is it bookable" logic -> CheckResult
│   ├── models.py            # Screening dataclass
│   ├── state.py             # JSON state file for de-duplicating notifications
│   ├── notify/
│   │   ├── __init__.py      # Notifier protocol + build_notifiers(settings)
│   │   ├── formatting.py    # shared text / HTML rendering
│   │   ├── email.py
│   │   └── discord.py
│   └── cli.py               # entrypoint: once / loop / list-films / dry-run
└── tests/
    ├── fixtures/
    │   ├── film_events_flora_2026-12-18.json   # captured real response (has Dune)
    │   └── film_events_empty.json
    ├── test_cinemacity.py
    ├── test_checker.py
    ├── test_state.py
    ├── test_formatting.py
    ├── test_discord.py
    └── test_email.py
```

Capture the real fixture with:

```bash
curl -s "https://www.cinemacity.cz/cz/data-api-service/v1/quickbook/10101/film-events/in-cinema/1052/at-date/2026-12-18?attr=&lang=en_GB" \
  | python -m json.tool > tests/fixtures/film_events_flora_2026-12-18.json
```

---

## 4. Implementation steps

### 4.1 `pyproject.toml` / tooling

- `uv init --package --python 3.14`, then `uv add httpx pydantic pydantic-settings tenacity` and `uv add --dev pytest respx ruff`.
- Console script: `imax-checker = "imax_checker.cli:main"`.
- Ruff: `target-version = "py314"`, line length 100, rule sets `E,F,I,UP,B,SIM`.

### 4.2 `config.py`: `Settings(BaseSettings)`

Precedence: CLI > environment variables (prefix `IMAX_`) > `.env` > defaults. Configure with `SettingsConfigDict(env_prefix="IMAX_", env_file=".env", cli_parse_args=True, cli_kebab_case=True, cli_implicit_flags=True)`.

| Field | Type | Default | Notes |
|---|---|---|---|
| `movie` | `str` | `"Dune: Part Three"` | |
| `cinema_id` | `str` | `"1052"` | OC Flora |
| `date_from` | `date` | `2026-12-27` | Accept `DD.MM.YYYY` **and** ISO `YYYY-MM-DD` (custom `field_validator(mode="before")`) |
| `date_to` | `date` | `2026-12-30` | inclusive; validate `date_from <= date_to` |
| `interval_minutes` | `PositiveInt` | `60` | |
| `once` | `bool` | `False` | run a single check and exit (for cron/systemd) |
| `dry_run` | `bool` | `False` | print notifications to stdout instead of sending |
| `lang` | `str` | `"en_GB"` | |
| `state_file` | `Path` | `.state/notified.json` | |
| `request_timeout_s` | `float` | `15.0` | |
| `smtp_host` | `str \| None` | `None` | email is disabled when host or recipients are missing |
| `smtp_port` | `int` | `587` | |
| `smtp_security` | `Literal["starttls","ssl","none"]` | `"starttls"` | |
| `smtp_username` / `smtp_password` | `str \| None` / `SecretStr \| None` | `None` | |
| `email_from` | `str \| None` | `None` | defaults to `smtp_username` |
| `email_recipients` | `list[str]` | `[]` | env: comma-separated **or** JSON list |
| `email_use_bcc` | `bool` | `True` | hide recipients from each other |
| `discord_webhook_urls` | `list[SecretStr]` | `[]` | multiple webhooks allowed; comma-separated or JSON |
| `log_level` | `str` | `"INFO"` | |

- For comma-separated list parsing from env, annotate the list fields with `Annotated[list[str], NoDecode]` (pydantic-settings ≥ 2.7) and split in a `before` validator.
- Validate emails with `pydantic.EmailStr` (`pydantic[email]` extra), or a lightweight check if you'd rather avoid the extra dependency.
- At startup, log a warning when no notifier is configured (and not in dry-run), and when dates in the range are already in the past. Skip past dates.

### 4.3 `cinemacity.py`: API client

- Pydantic models: `Film` (`id`, `name`, `attribute_ids`), `BookingInfo` (`block_online_sales`, `block_online_sales_until: datetime | None`), `Event` (`id`, `film_id`, `cinema_id`, `business_day: date`, `event_date_time: datetime`, `auditorium`, `auditorium_tiny_name`, `booking_link`, `booking_router_launch_link`, `composite_booking_link: BookingInfo | None`, `attribute_ids`, `availability_ratio: float | None`), `FilmEventsResponse` (`films`, `events`). Use `alias_generator=to_camel` and `extra="ignore"` so new upstream fields don't break parsing. `availability_ratio` defaults to `None` when the field is missing. Do not model `soldOut`.
- `class CinemaCityClient` wraps a single `httpx.Client(base_url=..., timeout=..., headers={"User-Agent": "imax-checker/0.1 (+contact email)", "Accept": "application/json"})`. Use it as a context manager.
  - `film_events(cinema_id, day) -> FilmEventsResponse`
  - `films(until) -> list[Film]` (for `--list-films`)
- Wrap requests in a `tenacity` retry: 3 attempts, exponential backoff with jitter, retrying on `httpx.TransportError`, 5xx and 429. Don't retry other 4xx; raise a clear `CinemaCityError`.
- Name matching helper `normalize(name)`: `casefold()`, Unicode NFKD with combining marks stripped, every non-alphanumeric run collapsed to a single space, then stripped. Match when `normalize(film.name) == normalize(movie)`. If nothing matches exactly, fall back to a substring match and log which film names matched, so `"Dune Part 3"`-style variants stay visible in the logs.

### 4.4 `models.py` + `checker.py`

```python
@dataclass(frozen=True, slots=True)
class Screening:
    event_id: str
    film_name: str
    starts_at: datetime  # tz-aware, Europe/Prague
    business_day: date
    auditorium: str
    booking_url: str  # bookingRouterLaunchLink, the page where the ticket can be bought
    availability_ratio: float | None = None  # fraction of seats still available; display only
```

`check(settings, client) -> CheckResult`:

1. Build the list of business days `date_from..date_to` inclusive, skipping days before today in Prague time.
2. For each day, call `film_events`. Keep going if one day fails: record it in `CheckResult.failed_days` and continue.
3. Find matching film ids in that day's `films`, filter `events` by those ids, then apply `is_imax` and the bookable rule from §1.
4. Return `CheckResult(bookable: list[Screening], non_imax_listed: int, checked_days: list[date], failed_days: list[date])`, with `bookable` holding IMAX screenings only, sorted by `starts_at`. Add a property `on_sale: bool` that is true when `bookable` is non-empty. `non_imax_listed` exists only for logging.
5. Log one summary line per run, for example `checked 4 days: IMAX tickets NOT on sale yet (2 non-IMAX screenings listed)` or `checked 4 days: IMAX tickets ON SALE (5 screenings)`.

### 4.5 `state.py`: de-duplicating notifications

Without state, an hourly job would send the same alert every hour. The main alert fires once, when sales open. Afterwards, send a follow-up only if new screenings become bookable (for example, 30.12 added a few days later). To do that, track per channel which screening ids have already been announced:

```json
{ "email": ["265001", "265002"], "discord": ["265001", "265002"] }
```

- `load()` / `save()` use atomic writes (write a temp file in the same directory, then `os.replace`) and create the parent directory.
- If `on_sale` is false, do nothing.
- For each channel, compute `new_ids = bookable_ids - already_notified[channel]`. If it's empty, send nothing. Otherwise send one message listing **all** currently bookable screenings, with the new ones marked, for example with a `NEW` tag on follow-ups.
- After a channel **succeeds**, add the ids to that channel's set. If one channel fails, only that channel retries next run.
- If saving the state file fails (for example a read-only disk), log an error and carry on with the remaining channels. The worst case is a repeated alert on the next run, which beats a missed one.
- Never remove ids from the state. A screening that disappears and reappears doesn't trigger another alert.

### 4.6 `notify/`: notifiers

```python
class Notifier(Protocol):
    name: str  # "email" | "discord" | "stdout"

    def send(
        self, screenings: Sequence[Screening], new_ids: Set[str], context: RunContext
    ) -> None: ...
```

`build_notifiers(settings)` returns the configured notifiers. In dry-run it returns only a `StdoutNotifier`.

**A failed notification never terminates the run.** If sending raises for any reason (any `Exception`, not only expected network/SMTP errors), the dispatcher:

1. logs an `ERROR` naming the channel, with the traceback (for example `Notifier 'discord' failed; will retry on the next run`),
2. doesn't record that channel's ids in the state, so it retries on the next run,
3. continues with the remaining channels and finishes the run normally. A failing SMTP server doesn't block Discord, and in loop mode the next check still happens on schedule.

Notification failures don't change the `--once` exit code. Only fetch failures do (see §4.7).

**Shared formatting (`formatting.py`):** group by date. Each line has the **date, weekday and time** (required), auditorium, availability, and booking link. Render `availability_ratio` as the nearest whole percent of seats still available (`0.3195` → `32% available`, `0` → `0% available`, `1` → `100% available`). Clamp to 0–100. When the ratio is missing, omit the label.

```
IMAX tickets on sale: Dune: Part Three @ Cinema City Flora

Sun 27.12.2026
  17:20  IMAX VOLVO  32% available  https://www.cinemacity.cz/cz/booking-router/launch/1052/265001?lang=en
  20:40  IMAX VOLVO  20% available  https://www.cinemacity.cz/cz/booking-router/launch/1052/265002?lang=en
Mon 28.12.2026
  ...
Checked range: 27.12.2026 – 30.12.2026 · cinema 1052
```

Render a screening that starts after midnight with its real date and time (for example `Thu 31.12.2026 00:01`).

**Email (`email.py`):**

- Build one `EmailMessage` per run. Subject: `IMAX tickets on sale: {movie} – {n} screening(s) {first_date}–{last_date}`. Add a plain-text body plus an HTML alternative (`msg.add_alternative(html, subtype="html")`) with clickable links.
- Recipients: put all of them in `Bcc` (when `email_use_bcc`) or in `To`, and send in a single `send_message` call.
- Connect according to `smtp_security`: `smtplib.SMTP_SSL` for `ssl`, otherwise `smtplib.SMTP` plus `starttls()` for `starttls`, with a timeout. Log in only if a username is set.
- Mention in the README that Gmail needs an App Password (`smtp.gmail.com:587`, starttls).

**Discord (`discord.py`):**

- `POST` to each webhook URL with `httpx`, sending `{"username": "IMAX Checker", "content": "...", "embeds": [...]}`.
- One embed per date, with one field per screening. Field name: `17:20 · IMAX VOLVO`. Field value: `32% available · [Book](url)`. On a follow-up, prefix a new screening with `NEW · `. Omit the percentage when `availability_ratio` is missing.
- Respect Discord limits: at most 10 embeds per message, 25 fields per embed, 6000 characters per message and 2000 per `content`. Split into several messages when needed.
- On `429`, sleep for `retry_after` from the JSON body and retry once; raise on any other non-2xx response.
- Webhooks are independent: catch **any** exception per webhook, keep posting to the remaining ones, then raise a single `DiscordError` summarizing the failed webhooks (by index, for example `webhook #1: HTTP 500`).
- Never log the webhook URL, since it is a secret (`SecretStr`). For unexpected exceptions, report only the exception type, because their messages may contain the URL.

### 4.7 `cli.py`: run modes

- `main()` parses settings via pydantic-settings (`CliApp.run` or `Settings()` with `cli_parse_args`), configures `logging` (timestamped format), then:
  - **`--once`:** run a single check and notify. Exit code `0` on success (including when a notifier failed, which is logged as an error), `1` if every day failed to fetch, `2` on a config error.
  - **Default loop:** run immediately, then every `interval_minutes`. Use `threading.Event().wait(timeout)` so SIGINT/SIGTERM handlers can stop it cleanly. Log the time of the next run. An exception in one run is logged and doesn't kill the loop. **Exit automatically** once `date_to` is in the past.
  - **`--list-films`:** print films (id, name) currently scheduled at the cinema in the date range, or until `date_to` if nothing is scheduled. Useful for finding the exact movie name.
  - **`--dry-run`:** works with both modes. It prints what would be sent and **doesn't write state**.
- Example invocations for the README:

```bash
uv run imax-checker --once --dry-run
uv run imax-checker --interval-minutes 30
uv run imax-checker --movie "Dune: Part Three" --date-from 27.12.2026 --date-to 30.12.2026
IMAX_INTERVAL_MINUTES=15 uv run imax-checker
```

### 4.8 `.env.example`

```dotenv
IMAX_MOVIE="Dune: Part Three"
IMAX_DATE_FROM=27.12.2026
IMAX_DATE_TO=30.12.2026
IMAX_INTERVAL_MINUTES=60

IMAX_SMTP_HOST=smtp.gmail.com
IMAX_SMTP_PORT=587
IMAX_SMTP_SECURITY=starttls
IMAX_SMTP_USERNAME=you@gmail.com
IMAX_SMTP_PASSWORD=app-password-here
IMAX_EMAIL_RECIPIENTS=alice@example.com,bob@example.com

IMAX_DISCORD_WEBHOOK_URLS=https://discord.com/api/webhooks/xxx/yyy
```

### 4.9 Deployment examples (README only, don't install anything)

- **cron** (`--once` mode): `0 * * * * cd /path/Imax_checker && /home/.../uv run imax-checker --once >> checker.log 2>&1`
- **systemd user timer**: `imax-checker.service` (`Type=oneshot`, `ExecStart=uv run imax-checker --once`, `WorkingDirectory=...`) plus `imax-checker.timer` (`OnCalendar=hourly`, `Persistent=true`).
- **Long-running loop**: `uv run imax-checker` in tmux, or as a systemd service with `Restart=on-failure`.

---

## 5. Tests (`uv run pytest`)

Tests make no real network calls. Use `respx` for httpx and monkeypatch `smtplib`.

- **cinemacity:** parses the real fixture; parses the empty fixture; ignores unknown fields; retries on 503 then succeeds; raises `CinemaCityError` on 404.
- **checker:**
  - The 2026-12-18 fixture with movie `Dune: Part Three` gives only Dune screenings, sorted, with Prague-aware datetimes.
  - Name normalization: `"dune part three"` and `"DUNE: PART THREE"` match.
  - Events are excluded when `blockOnlineSales=true` with no `blockOnlineSalesUntil`, when `blockOnlineSalesUntil` is in the future, or when the booking link is missing.
  - Events with `soldOut=true` or `availabilityRatio=0` are **still bookable**. The ratio is copied onto the screening (`0` stays `0`); `soldOut` is not stored.
  - `on_sale` is true with one bookable screening in the range and false with none.
  - The 2026-12-18 fixture contains Dune in both `IMAX VOLVO` and `Sál 01`; only the `IMAX VOLVO` screenings are returned and `non_imax_listed` counts the `Sál 01` ones.
  - `is_imax` is true for auditorium `IMAX VOLVO`, for tiny name `IMAX` with any auditorium name, and for a regular hall whose event has the `imax` attribute. It's false for `Sál 01` with attributes `["2d"]`.
  - A day where the movie plays only in non-IMAX halls gives `on_sale == False`.
  - The empty-day fixture gives no results and no error.
  - One day returning 500 lands in `failed_days` while the other days still return results.
  - Past days are skipped (freeze "today" by injecting a clock or `today` parameter instead of adding freezegun).
- **date parsing:** `27.12.2026` and `2026-12-27` both parse; `date_from > date_to` raises a validation error.
- **state:** not on sale sends nothing; the first run that's on sale notifies with all screenings; a second run with the same data sends nothing; a newly added screening sends a follow-up listing everything with only that one marked `NEW`; after a channel fails, the next run retries that channel only; a notifier raising an arbitrary exception (for example `KeyError`) logs an error and the other channels are still notified; a state-save failure logs an error and doesn't stop the remaining channels; an id that disappears and reappears doesn't re-notify; writes are atomic.
- **formatting:** the output contains the weekday, date (`27.12.2026`), time (`17:20`), booking URL, and availability (`0.3195` → `32% available`; `0` → `0% available`). A missing ratio omits the label. An after-midnight screening shows the next calendar date.
- **discord:** the payload has the expected structure; 30 screenings are split across several messages within the limits; a 429 with `retry_after` is retried; an unexpected exception on one webhook doesn't skip the others and its message (which may hold the URL) isn't reported; the webhook URL never shows up in logs.
- **email:** recipients end up in `Bcc`; the message has text and HTML parts; `starttls` or `SMTP_SSL` is chosen per config; login is skipped without a username.
- **cli:** `--once --dry-run` with respx-mocked API data prints the expected screenings and leaves the state file untouched; `--once` with a Discord webhook returning 500 prints an `ERROR` to stderr (without the webhook URL), exits `0`, and writes no state for Discord.

---

## 6. Manual verification (agent must run these)

1. `uv run ruff check . && uv run ruff format --check . && uv run pytest`: everything passes.
2. **Live smoke test against dates that already have Dune screenings:**
   `uv run imax-checker --once --dry-run --date-from 18.12.2026 --date-to 18.12.2026`
   This should print the Dune: Part Three **IMAX VOLVO** screenings (10:40, 14:00, 17:20 and 20:40 as of 2026-10-21), each with its availability percentage. Each line's link is `bookingRouterLaunchLink` (`https://www.cinemacity.cz/cz/booking-router/launch/1052/{eventId}?lang=en`), which opens the ticket site, not `https://tickets.cinemacity.cz/api/order/...`. The 18:00 `Sál 01` screening must **not** appear.
3. **Live run with the defaults:** `uv run imax-checker --once --dry-run` should log that 4 days were checked and tickets are not on sale yet (27–30 December isn't scheduled yet as of 2026-10-21), and send nothing.
4. `uv run imax-checker --list-films --date-from 15.12.2026 --date-to 20.12.2026` should list `8105s2r  Dune: Part Three`.
5. If the user has provided real SMTP or Discord credentials in `.env`, run step 2 **without** `--dry-run` once to confirm delivery. Then delete `.state/notified.json` so the real run starts clean.

---

## 7. Acceptance criteria

- [ ] Defaults: `Dune: Part Three`, Flora `1052`, `27.12.2026`–`30.12.2026` inclusive, every 60 minutes. Every value can be overridden via CLI or env.
- [ ] Uses the public JSON API. No HTML scraping and no browser.
- [ ] Checks **every IMAX** screening (all time slots) for each day in the range, including after-midnight screenings that belong to the last business day. IMAX is detected by auditorium name or tiny name, not only by the unreliable `imax` attribute.
- [ ] Non-IMAX screenings of the movie never trigger or appear in a notification.
- [ ] Notifies as soon as tickets can be bought for **at least one IMAX** screening in the range. `availabilityRatio` does not gate the alert, and `soldOut` is ignored: a screening at ratio `0` is still listed.
- [ ] Notifies by email (multiple recipients) and Discord (one or more webhooks). Each notification lists the date and time of every bookable IMAX screening in the range, plus auditorium, the percentage of seats still available (`availabilityRatio` rounded to a whole percent), and a link to the Cinema City page where that screening can be bought (`bookingRouterLaunchLink`). The `/api/order/` URL is not shown.
- [ ] Sends the main alert once when sales open, then follow-ups only when new screenings become bookable. No hourly repeats.
- [ ] A failure in one day's fetch doesn't break the rest of the run or the loop.
- [ ] A notification that fails to send for any reason (any exception, in any notifier or webhook, or while saving state afterwards) never terminates the run: an error is logged and the run continues with the remaining channels.
- [ ] Runs as a long-lived loop or a single `--once` run; stops by itself after the date range has passed.
- [ ] Secrets only in `.env` (gitignored) and never logged.
- [ ] Tests and ruff pass on Python 3.14.

## 8. Out of scope

- Buying or reserving tickets.
- Sold-out filtering, occupancy thresholds, an exact free-seat count, or seat-map checks. `availabilityRatio` is display-only.
- Non-IMAX formats (regular 2D, 4DX, VIP, and so on), and making the hall or format configurable.
- Multiple cinemas or movies in one run. Keep `cinema_id` and `movie` single-valued but structure the code so both could become lists later.
