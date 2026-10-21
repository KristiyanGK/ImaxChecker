import logging
import time
from collections.abc import Callable, Sequence
from collections.abc import Set as AbstractSet
from typing import Any

import httpx

from imax_checker.config import Settings
from imax_checker.models import RunContext, Screening
from imax_checker.notify.formatting import (
    fmt_availability,
    fmt_day_heading,
    fmt_time,
    group_by_date,
    is_followup,
    render_footer,
    render_title,
)

log = logging.getLogger(__name__)

USERNAME = "IMAX Checker"
MAX_CONTENT = 2000
MAX_EMBEDS = 10
MAX_FIELDS = 25
MAX_MESSAGE_CHARS = 6000
MAX_RETRY_AFTER_S = 60.0

type Payload = dict[str, Any]


class DiscordError(Exception):
    """Delivery failed. Messages never contain the (secret) webhook URL."""


def _embed_size(embed: Payload) -> int:
    size = len(embed.get("title", "")) + len(embed.get("footer", {}).get("text", ""))
    for field in embed.get("fields", []):
        size += len(field["name"]) + len(field["value"])
    return size


def build_payloads(
    screenings: Sequence[Screening], new_ids: AbstractSet[str], context: RunContext
) -> list[Payload]:
    """Build webhook payloads that respect Discord's embed/field/character limits."""
    followup = is_followup(screenings, new_ids)
    footer = {"text": render_footer(context)}

    embeds: list[Payload] = []
    for day, group in group_by_date(screenings):
        heading = fmt_day_heading(day)
        for start in range(0, len(group), MAX_FIELDS):
            chunk = group[start : start + MAX_FIELDS]
            fields = []
            for s in chunk:
                parts = []
                if followup and s.event_id in new_ids:
                    parts.append("NEW")
                label = fmt_availability(s.availability_ratio)
                if label:
                    parts.append(label)
                parts.append(f"[Book]({s.booking_url})")
                fields.append(
                    {
                        "name": f"{fmt_time(s)} · {s.auditorium}",
                        "value": " · ".join(parts),
                        "inline": True,
                    }
                )
            title = heading if start == 0 else f"{heading} (cont.)"
            embeds.append({"title": title, "fields": fields})

    # Pack embeds into messages without exceeding the per-message limits.
    groups: list[list[Payload]] = []
    current: list[Payload] = []
    size = len(footer["text"])
    for embed in embeds:
        embed_size = _embed_size(embed)
        if current and (len(current) >= MAX_EMBEDS or size + embed_size > MAX_MESSAGE_CHARS):
            groups.append(current)
            current, size = [], len(footer["text"])
        current.append(embed)
        size += embed_size
    if current:
        groups.append(current)

    title = render_title(context, followup=followup)
    payloads: list[Payload] = []
    for index, group in enumerate(groups, start=1):
        group[-1]["footer"] = footer
        content = title if len(groups) == 1 else f"{title} (part {index}/{len(groups)})"
        payloads.append({"username": USERNAME, "content": content[:MAX_CONTENT], "embeds": group})
    return payloads


class DiscordNotifier:
    name = "discord"

    def __init__(
        self,
        webhook_urls: Sequence[str],
        *,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # httpx logs every request URL at INFO level, which would leak the webhook secret.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        self._urls = list(webhook_urls)
        self._client = client or httpx.Client(timeout=timeout)
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> DiscordNotifier:
        return cls(
            [url.get_secret_value() for url in settings.discord_webhook_urls],
            timeout=settings.request_timeout_s,
        )

    def _post(self, url: str, payload: Payload) -> None:
        for attempt in (1, 2):
            try:
                response = self._client.post(url, json=payload)
            except httpx.HTTPError as exc:
                raise DiscordError(f"request failed: {type(exc).__name__}") from None
            if response.status_code == 429 and attempt == 1:
                try:
                    retry_after = float(response.json().get("retry_after", 1))
                except ValueError, AttributeError:
                    retry_after = 1.0
                wait = min(max(retry_after, 0.0), MAX_RETRY_AFTER_S)
                log.warning("Discord rate limited, retrying in %.1fs", wait)
                self._sleep(wait)
                continue
            if not response.is_success:
                raise DiscordError(f"HTTP {response.status_code}: {response.text[:200]}")
            return

    def send(
        self, screenings: Sequence[Screening], new_ids: AbstractSet[str], context: RunContext
    ) -> None:
        payloads = build_payloads(screenings, new_ids, context)
        errors: list[str] = []
        for index, url in enumerate(self._urls, start=1):
            try:
                for payload in payloads:
                    self._post(url, payload)
            except DiscordError as exc:
                errors.append(f"webhook #{index}: {exc}")
            except Exception as exc:
                # The message of an arbitrary exception may embed the secret webhook URL.
                errors.append(f"webhook #{index}: unexpected {type(exc).__name__}")
        if errors:
            raise DiscordError("; ".join(errors))
        log.info("Discord message sent to %d webhook(s)", len(self._urls))
