from datetime import date

import pytest
from pydantic import ValidationError

from imax_checker.config import load_settings

from .conftest import make_settings


def test_defaults():
    s = make_settings()
    assert s.movie == "Dune: Part Three"
    assert s.cinema_id == "1052"
    assert (s.date_from, s.date_to) == (date(2026, 12, 27), date(2026, 12, 30))
    assert s.interval_minutes == 60
    assert not s.email_enabled
    assert not s.discord_enabled


@pytest.mark.parametrize("value", ["27.12.2026", "2026-12-27"])
def test_both_date_formats_parse(value):
    assert make_settings(date_from=value).date_from == date(2026, 12, 27)


def test_invalid_dotted_date_is_rejected():
    with pytest.raises(ValidationError):
        make_settings(date_from="31.31.2026")


def test_date_from_after_date_to_is_rejected():
    with pytest.raises(ValidationError, match="must not be after"):
        make_settings(date_from="2026-12-31", date_to="2026-12-30")


def test_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("IMAX_MOVIE", "From Env")
    monkeypatch.setenv("IMAX_INTERVAL_MINUTES", "15")
    s = load_settings(["--movie", "From CLI", "--once", "--dry-run", "--date-from", "18.12.2026"])
    assert s.movie == "From CLI"
    assert s.interval_minutes == 15
    assert s.once and s.dry_run
    assert s.date_from == date(2026, 12, 18)


def test_comma_separated_and_json_lists_from_env(monkeypatch):
    monkeypatch.setenv("IMAX_EMAIL_RECIPIENTS", "a@example.com, b@example.com")
    monkeypatch.setenv("IMAX_DISCORD_WEBHOOK_URLS", '["https://x/1", "https://x/2"]')
    s = make_settings()
    assert [str(r) for r in s.email_recipients] == ["a@example.com", "b@example.com"]
    assert [u.get_secret_value() for u in s.discord_webhook_urls] == ["https://x/1", "https://x/2"]


def test_invalid_recipient_is_rejected():
    with pytest.raises(ValidationError):
        make_settings(email_recipients="not-an-email")


def test_email_needs_host_recipients_and_sender():
    assert not make_settings(smtp_host="h", email_recipients="a@example.com").email_enabled
    s = make_settings(smtp_host="h", email_recipients="a@example.com", smtp_username="me@x.com")
    assert s.email_enabled
    assert s.email_sender == "me@x.com"


def test_secrets_are_not_in_repr():
    s = make_settings(smtp_password="hunter2", discord_webhook_urls="https://discord/secret")
    assert "hunter2" not in repr(s)
    assert "discord/secret" not in repr(s)
