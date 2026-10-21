import logging
import signal
import sys
import threading
from collections.abc import Sequence
from datetime import datetime, timedelta

from pydantic import ValidationError

from imax_checker.checker import CheckResult, check, date_range, prague_today
from imax_checker.cinemacity import PRAGUE, CinemaCityClient, CinemaCityError, Film
from imax_checker.config import Settings, load_settings
from imax_checker.models import RunContext
from imax_checker.notify import Notifier, build_notifiers, dispatch

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FETCH_FAILED = 1
EXIT_CONFIG_ERROR = 2


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    # httpx logs full request URLs at INFO; Discord webhook URLs are secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def make_context(settings: Settings) -> RunContext:
    return RunContext(
        movie=settings.movie,
        cinema_id=settings.cinema_id,
        date_from=settings.date_from,
        date_to=settings.date_to,
    )


def make_client(settings: Settings) -> CinemaCityClient:
    return CinemaCityClient(lang=settings.lang, timeout=settings.request_timeout_s)


def warn_about_setup(settings: Settings, notifiers: Sequence[Notifier]) -> None:
    if not settings.dry_run and not notifiers:
        log.warning(
            "No notifier configured (set IMAX_SMTP_HOST + IMAX_EMAIL_RECIPIENTS and/or "
            "IMAX_DISCORD_WEBHOOK_URLS); results will only be logged"
        )
    if not settings.dry_run and settings.smtp_host and not settings.email_enabled:
        log.warning("Email is disabled: SMTP host is set but recipients or sender are missing")
    today = prague_today()
    past = [d for d in date_range(settings.date_from, settings.date_to) if d < today]
    if past:
        log.warning("%d day(s) of the range are already in the past and will be skipped", len(past))


def run_check(settings: Settings, notifiers: Sequence[Notifier]) -> CheckResult:
    with make_client(settings) as client:
        result = check(settings, client)
    dispatch(
        result,
        notifiers,
        make_context(settings),
        settings.state_file,
        persist=not settings.dry_run,
    )
    return result


def run_once(settings: Settings, notifiers: Sequence[Notifier]) -> int:
    result = run_check(settings, notifiers)
    if result.failed_days and not result.checked_days:
        log.error("Every day failed to fetch")
        return EXIT_FETCH_FAILED
    return EXIT_OK


def run_loop(settings: Settings, notifiers: Sequence[Notifier], stop: threading.Event) -> int:
    interval_s = settings.interval_minutes * 60
    while not stop.is_set():
        if prague_today() > settings.date_to:
            log.info("Date range ended on %s; exiting", settings.date_to.strftime("%d.%m.%Y"))
            return EXIT_OK
        try:
            run_check(settings, notifiers)
        except Exception:
            log.exception("Check failed; will try again on the next interval")
        next_run = datetime.now(PRAGUE) + timedelta(seconds=interval_s)
        log.info("Next check at %s", next_run.strftime("%Y-%m-%d %H:%M:%S %Z"))
        stop.wait(interval_s)
    log.info("Stopped")
    return EXIT_OK


def list_films(settings: Settings) -> int:
    today = prague_today()
    found: dict[str, Film] = {}
    with make_client(settings) as client:
        for day in date_range(settings.date_from, settings.date_to):
            if day < today:
                continue
            try:
                response = client.film_events(settings.cinema_id, day)
            except CinemaCityError as exc:
                log.warning("Fetching %s failed: %s", day, exc)
                continue
            found.update((film.id, film) for film in response.films)
        if not found:
            log.info(
                "Nothing scheduled at cinema %s in the range; listing all films until %s",
                settings.cinema_id,
                settings.date_to,
            )
            try:
                found.update((film.id, film) for film in client.films(settings.date_to))
            except CinemaCityError as exc:
                log.error("Could not list films: %s", exc)
                return EXIT_FETCH_FAILED
    for film in sorted(found.values(), key=lambda f: f.name.casefold()):
        print(f"{film.id}  {film.name}")
    return EXIT_OK


def run(settings: Settings) -> int:
    configure_logging(settings.log_level)
    if settings.list_films:
        return list_films(settings)

    notifiers = build_notifiers(settings)
    warn_about_setup(settings, notifiers)

    if settings.once:
        return run_once(settings, notifiers)

    stop = threading.Event()

    def handle_signal(signum: int, _frame: object) -> None:
        log.info("Received %s, shutting down", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    return run_loop(settings, notifiers, stop)


def main(argv: Sequence[str] | None = None) -> None:
    try:
        settings = load_settings(argv)
    except ValidationError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)
    sys.exit(run(settings))
