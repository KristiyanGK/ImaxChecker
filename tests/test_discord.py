import json
import logging
from datetime import date, timedelta

import httpx
import pytest
import respx

from imax_checker.notify.discord import (
    MAX_CONTENT,
    MAX_EMBEDS,
    MAX_FIELDS,
    MAX_MESSAGE_CHARS,
    DiscordError,
    DiscordNotifier,
    build_payloads,
)

from .conftest import make_screening

WEBHOOK = "https://discord.com/api/webhooks/123456/super-secret-token"


def payload_size(payload) -> int:
    size = len(payload["content"])
    for embed in payload["embeds"]:
        size += len(embed.get("title", "")) + len(embed.get("footer", {}).get("text", ""))
        size += sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
    return size


def assert_within_limits(payloads):
    for p in payloads:
        assert len(p["content"]) <= MAX_CONTENT
        assert len(p["embeds"]) <= MAX_EMBEDS
        assert payload_size(p) <= MAX_MESSAGE_CHARS
        assert all(len(e["fields"]) <= MAX_FIELDS for e in p["embeds"])


def test_payload_structure(context):
    s = [make_screening("1"), make_screening("2", "2026-12-28T20:40:00")]
    (payload,) = build_payloads(s, {"1", "2"}, context)

    assert payload["username"] == "IMAX Checker"
    assert payload["content"] == "IMAX tickets on sale: Dune: Part Three @ Cinema City Flora"
    first, second = payload["embeds"]
    assert first["title"] == "Sun 27.12.2026"
    assert first["fields"][0]["name"] == "17:20 · IMAX VOLVO"
    assert (
        first["fields"][0]["value"]
        == "50% available · [Book](https://www.cinemacity.cz/cz/booking-router/launch/1052/1?lang=en)"
    )
    assert second["title"] == "Mon 28.12.2026"
    assert "27.12.2026" in second["footer"]["text"]  # footer on the last embed
    assert "footer" not in first


def test_followup_prefixes_new(context):
    s = [make_screening("1"), make_screening("2", "2026-12-27T20:40:00")]
    (payload,) = build_payloads(s, {"2"}, context)
    values = [f["value"] for f in payload["embeds"][0]["fields"]]
    assert values[0].startswith("50% available · [Book]")
    assert values[1].startswith("NEW · 50% available · [Book]")


def test_30_screenings_on_30_days_are_split_across_messages(context):
    start = date(2026, 12, 1)
    s = [
        make_screening(str(i), f"{(start + timedelta(days=i)).isoformat()}T17:20:00")
        for i in range(30)
    ]
    payloads = build_payloads(s, {x.event_id for x in s}, context)
    assert len(payloads) >= 3
    assert_within_limits(payloads)
    assert sum(len(e["fields"]) for p in payloads for e in p["embeds"]) == 30
    assert "part 1/" in payloads[0]["content"]


def test_30_screenings_on_one_day_split_into_embeds_of_25(context):
    s = [
        make_screening(str(i), f"2026-12-27T{10 + i // 4:02d}:{(i % 4) * 15:02d}:00")
        for i in range(30)
    ]
    payloads = build_payloads(s, {x.event_id for x in s}, context)
    assert_within_limits(payloads)
    embeds = [e for p in payloads for e in p["embeds"]]
    assert [len(e["fields"]) for e in embeds] == [25, 5]
    assert embeds[1]["title"].endswith("(cont.)")


def test_long_auditorium_names_still_respect_char_limit(context):
    s = [
        make_screening(str(i), f"2026-12-{1 + i:02d}T17:20:00", auditorium="IMAX " + "X" * 200)
        for i in range(25)
    ]
    assert_within_limits(build_payloads(s, {x.event_id for x in s}, context))


@respx.mock
def test_sends_to_every_webhook(context):
    r1 = respx.post(WEBHOOK).respond(204)
    r2 = respx.post("https://discord.com/api/webhooks/9/other").respond(204)
    notifier = DiscordNotifier([WEBHOOK, "https://discord.com/api/webhooks/9/other"])
    notifier.send([make_screening("1")], {"1"}, context)
    assert r1.call_count == r2.call_count == 1
    body = json.loads(r1.calls.last.request.content)
    assert body["embeds"][0]["fields"][0]["name"] == "17:20 · IMAX VOLVO"


@respx.mock
def test_429_is_retried_once_after_retry_after(context):
    route = respx.post(WEBHOOK).mock(
        side_effect=[httpx.Response(429, json={"retry_after": 0.5}), httpx.Response(204)]
    )
    sleeps: list[float] = []
    DiscordNotifier([WEBHOOK], sleep=sleeps.append).send([make_screening("1")], {"1"}, context)
    assert route.call_count == 2
    assert sleeps == [0.5]


@respx.mock
def test_second_429_raises(context):
    respx.post(WEBHOOK).respond(429, json={"retry_after": 0})
    with pytest.raises(DiscordError, match="429"):
        DiscordNotifier([WEBHOOK], sleep=lambda s: None).send([make_screening("1")], {"1"}, context)


@respx.mock
def test_non_2xx_raises_without_leaking_url(context):
    respx.post(WEBHOOK).respond(500, text="server error")
    with pytest.raises(DiscordError) as excinfo:
        DiscordNotifier([WEBHOOK]).send([make_screening("1")], {"1"}, context)
    assert "super-secret-token" not in str(excinfo.value)
    assert "HTTP 500" in str(excinfo.value)


@respx.mock
def test_transport_error_raises_without_leaking_url(context):
    respx.post(WEBHOOK).mock(side_effect=httpx.ConnectError("nope"))
    with pytest.raises(DiscordError) as excinfo:
        DiscordNotifier([WEBHOOK]).send([make_screening("1")], {"1"}, context)
    assert "super-secret-token" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


@respx.mock
def test_unexpected_error_on_one_webhook_does_not_skip_the_others(context):
    respx.post(WEBHOOK).mock(side_effect=ValueError(f"bad url {WEBHOOK}"))
    other = respx.post("https://discord.com/api/webhooks/9/other").respond(204)
    notifier = DiscordNotifier([WEBHOOK, "https://discord.com/api/webhooks/9/other"])
    with pytest.raises(DiscordError) as excinfo:
        notifier.send([make_screening("1")], {"1"}, context)
    assert other.call_count == 1
    assert "webhook #1: unexpected ValueError" in str(excinfo.value)
    assert "super-secret-token" not in str(excinfo.value)


@respx.mock
def test_webhook_url_never_appears_in_logs(context, caplog):
    respx.post(WEBHOOK).respond(204)
    with caplog.at_level(logging.DEBUG):
        DiscordNotifier([WEBHOOK]).send([make_screening("1")], {"1"}, context)
    assert caplog.records  # something was logged
    assert "super-secret-token" not in caplog.text
    assert "123456" not in caplog.text
