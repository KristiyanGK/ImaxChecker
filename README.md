# IMAX checker

Checks every hour (configurable) whether tickets can be bought for **at least one IMAX screening**
of a movie at **Cinema City – OC Flora (Prague, cinema id `1052`)** within a date range. When they
can, it notifies by **email (multiple recipients)** and **Discord**, listing the date and time of
every bookable IMAX screening in the range.

Defaults: movie `Dune: Part Three`, dates `27.12.2026` – `30.12.2026` (inclusive).

Rules worth knowing:

- **Only IMAX screenings count.** Screenings in regular halls (e.g. `Sál 01`) never trigger a
  notification and never appear in one. IMAX is detected by the auditorium name (`IMAX VOLVO`) or
  tiny name, with the `imax` attribute only as a safety net.
- The tool watches for **ticket sales opening**. Each screening in a notification includes
  the percentage of seats still available (`availabilityRatio`, shown as `32% available`).
  That percentage does not decide whether tickets are on sale, and `soldOut` is ignored.
- The main alert fires **once**, when sales open. Afterwards a follow-up is sent only when new
  screenings become bookable (marked `NEW`). State lives in `.state/notified.json`, tracked per
  channel, so a channel that failed is retried on the next run without repeating the others.

## How it works

The Cinema City booking page is a client-side app backed by a public JSON API
(`https://www.cinemacity.cz/cz/data-api-service/v1/quickbook/10101/...`). No auth is needed, so
this tool calls the API directly (about 4 requests per run) instead of scraping HTML.

> If the API ever breaks, the fallback would be Playwright loading the SPA, since the DOM is
> rendered client-side. That fallback is deliberately not implemented.

## Setup

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then edit: SMTP and/or Discord settings
```

Secrets only belong in `.env` (gitignored). They are never logged. Gmail needs an
[App Password](https://myaccount.google.com/apppasswords) (`smtp.gmail.com:587`, `starttls`).

## Configuration

Precedence: CLI > environment variables (`IMAX_` prefix) > `.env` > defaults. Every setting is
documented in [`.env.example`](.env.example); CLI flags use kebab-case (`--date-from`,
`--interval-minutes`, ...). Dates accept `DD.MM.YYYY` and `YYYY-MM-DD`.

| Setting | Default |
|---|---|
| `movie` | `Dune: Part Three` |
| `cinema_id` | `1052` |
| `date_from` / `date_to` | `27.12.2026` / `30.12.2026` |
| `interval_minutes` | `60` |
| `once` | `false` |
| `dry_run` | `false` |
| `list_films` | `false` |
| `state_file` | `.state/notified.json` |
| `smtp_*`, `email_*`, `discord_webhook_urls` | unset: channel disabled |

## Running

```bash
uv run imax-checker --once --dry-run                       # single check, print instead of sending
uv run imax-checker --interval-minutes 30                  # loop
uv run imax-checker --movie "Dune: Part Three" --date-from 27.12.2026 --date-to 30.12.2026
IMAX_INTERVAL_MINUTES=15 uv run imax-checker
uv run imax-checker --list-films --date-from 15.12.2026 --date-to 20.12.2026
```

- **Loop (default):** checks immediately, then every `interval_minutes`. Stops cleanly on
  SIGINT/SIGTERM and **exits by itself** once `date_to` is in the past.
- **`--once`:** one check, then exit. Exit code `0` ok, `1` if every day failed to fetch, `2` on a
  configuration error.
- **`--dry-run`:** prints what would be sent and **does not read or write state**.
- **`--list-films`:** prints `id  name` of films scheduled at the cinema in the date range (or all
  films until `date_to` if nothing is scheduled). Handy for finding the exact movie name.

Past days in the range are skipped. Days with no schedule yet are a normal state, not an error.

## Deployment examples

**cron** (`--once` mode):

```cron
0 * * * * cd /path/to/Imax_checker && /home/you/.local/bin/uv run imax-checker --once >> checker.log 2>&1
```

**systemd user timer** (`~/.config/systemd/user/`):

```ini
# imax-checker.service
[Unit]
Description=IMAX tickets checker

[Service]
Type=oneshot
WorkingDirectory=/path/to/Imax_checker
ExecStart=/home/you/.local/bin/uv run imax-checker --once
```

```ini
# imax-checker.timer
[Unit]
Description=Run IMAX checker hourly

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

Enable with `systemctl --user enable --now imax-checker.timer`.

**Long-running loop:** run `uv run imax-checker` in tmux, or as a systemd service with
`Restart=on-failure` (`ExecStart=uv run imax-checker`).

## Development

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

Tests make no real network calls (`respx` for HTTP, a fake `smtplib`). Layout:

```
src/imax_checker/
  config.py       Settings (pydantic-settings)
  cinemacity.py   API client + response models + movie-name matching
  checker.py      is_imax / bookable rules -> CheckResult
  state.py        atomic JSON state for de-duplication
  notify/         email, discord, stdout + shared formatting + dispatch
  cli.py          once / loop / list-films / dry-run
```
