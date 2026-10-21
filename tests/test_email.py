import smtplib

import pytest

from imax_checker.notify.email import EmailNotifier

from .conftest import make_screening


class FakeSMTP:
    instances: list[FakeSMTP] = []
    kind = "SMTP"

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.messages = []
        self.login_args = None
        FakeSMTP.instances.append(self)

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append("login")
        self.login_args = (user, password)

    def send_message(self, msg):
        self.calls.append("send_message")
        self.messages.append(msg)

    def quit(self):
        self.calls.append("quit")

    def close(self):
        self.calls.append("close")


class FakeSMTPSSL(FakeSMTP):
    kind = "SMTP_SSL"


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP


def notifier(**overrides) -> EmailNotifier:
    params = dict(
        host="smtp.example.com",
        port=587,
        security="starttls",
        username="me@example.com",
        password="pw",
        sender="me@example.com",
        recipients=["a@example.com", "b@example.com"],
    )
    params.update(overrides)
    return EmailNotifier(**params)


def send(n: EmailNotifier, context):
    n.send([make_screening("1"), make_screening("2", "2026-12-28T20:40:00")], {"1", "2"}, context)
    return FakeSMTP.instances[-1]


def test_recipients_in_bcc_by_default(context):
    smtp = send(notifier(), context)
    (msg,) = smtp.messages
    assert msg["Bcc"] == "a@example.com, b@example.com"
    assert msg["To"] is None
    assert msg["From"] == "me@example.com"
    assert msg["Subject"].startswith("IMAX tickets on sale: Dune: Part Three – 2 screenings")


def test_recipients_in_to_without_bcc(context):
    (msg,) = send(notifier(use_bcc=False), context).messages
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg["Bcc"] is None


def test_message_has_text_and_html_parts(context):
    (msg,) = send(notifier(), context).messages
    assert msg.is_multipart()
    text = msg.get_body(preferencelist=("plain",)).get_content()
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert "Sun 27.12.2026" in text
    assert "50% available" in text
    assert "https://www.cinemacity.cz/cz/booking-router/launch/1052/1?lang=en" in text
    assert "50% available" in html
    assert '<a href="https://www.cinemacity.cz/cz/booking-router/launch/1052/1?lang=en">' in html


def test_starttls_flow(context):
    smtp = send(notifier(security="starttls"), context)
    assert smtp.kind == "SMTP"
    assert smtp.calls == ["starttls", "login", "send_message", "quit"]
    assert smtp.login_args == ("me@example.com", "pw")
    assert smtp.timeout is not None


def test_ssl_flow(context):
    smtp = send(notifier(security="ssl", port=465), context)
    assert smtp.kind == "SMTP_SSL"
    assert "starttls" not in smtp.calls
    assert smtp.port == 465


def test_plain_flow(context):
    smtp = send(notifier(security="none", port=25), context)
    assert smtp.kind == "SMTP"
    assert "starttls" not in smtp.calls


def test_login_skipped_without_username(context):
    smtp = send(notifier(username=None, password=None), context)
    assert "login" not in smtp.calls
    assert smtp.calls[-2:] == ["send_message", "quit"]


def test_quit_even_when_send_fails(context, monkeypatch):
    class Failing(FakeSMTP):
        def send_message(self, msg):
            raise smtplib.SMTPException("rejected")

    monkeypatch.setattr(smtplib, "SMTP", Failing)
    with pytest.raises(smtplib.SMTPException):
        notifier().send([make_screening("1")], {"1"}, context)
    assert FakeSMTP.instances[-1].calls[-1] == "quit"
