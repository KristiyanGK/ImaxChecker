import logging
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Protocol

from imax_checker import state as state_store
from imax_checker.checker import CheckResult
from imax_checker.config import Settings
from imax_checker.models import RunContext, Screening
from imax_checker.notify.formatting import render_text

log = logging.getLogger(__name__)


class Notifier(Protocol):
    name: str  # "email" | "discord" | "stdout"

    def send(
        self, screenings: Sequence[Screening], new_ids: AbstractSet[str], context: RunContext
    ) -> None: ...


class StdoutNotifier:
    name = "stdout"

    def send(
        self, screenings: Sequence[Screening], new_ids: AbstractSet[str], context: RunContext
    ) -> None:
        print(render_text(screenings, new_ids, context), flush=True)


def build_notifiers(settings: Settings) -> list[Notifier]:
    if settings.dry_run:
        return [StdoutNotifier()]
    # Imported lazily so each channel's dependencies are only loaded when it is configured.
    from imax_checker.notify.discord import DiscordNotifier
    from imax_checker.notify.email import EmailNotifier

    notifiers: list[Notifier] = []
    if settings.email_enabled:
        notifiers.append(EmailNotifier.from_settings(settings))
    if settings.discord_enabled:
        notifiers.append(DiscordNotifier.from_settings(settings))
    return notifiers


def dispatch(
    result: CheckResult,
    notifiers: Sequence[Notifier],
    context: RunContext,
    state_file: Path,
    *,
    persist: bool = True,
) -> list[str]:
    """Notify every channel that has not yet announced all currently bookable screenings.

    Returns the names of the channels that were notified successfully. With `persist=False`
    (dry run) the state file is neither read nor written.
    """
    if not result.on_sale:
        return []

    state = state_store.load(state_file) if persist else {}
    bookable_ids = {s.event_id for s in result.bookable}
    notified: list[str] = []

    for notifier in notifiers:
        already = state.get(notifier.name, set())
        new_ids = bookable_ids - already
        if not new_ids:
            log.info("%s: nothing new to announce", notifier.name)
            continue
        try:
            notifier.send(result.bookable, new_ids, context)
        except Exception:
            # One failing channel must not block the others; it retries on the next run.
            log.exception("Notifier %r failed; will retry on the next run", notifier.name)
            continue
        notified.append(notifier.name)
        state[notifier.name] = already | bookable_ids
        if persist:
            try:
                state_store.save(state_file, state)
            except Exception:
                log.exception(
                    "Could not save state file %s; %s may repeat this alert on the next run",
                    state_file,
                    notifier.name,
                )
    return notified
