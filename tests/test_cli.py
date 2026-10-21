import threading
from datetime import date

import pytest
import respx
from tenacity import wait_none

from imax_checker import cli
from imax_checker.cinemacity import BASE_URL, CinemaCityClient

from .conftest import TODAY, load_fixture, make_settings

DAY = "2026-12-18"
URL = f"{BASE_URL}/film-events/in-cinema/1052/at-date/{DAY}"


@pytest.fixture(autouse=True)
def freeze_today(monkeypatch):
    monkeypatch.setattr(cli, "prague_today", lambda: TODAY)
    monkeypatch.setattr("imax_checker.checker.prague_today", lambda: TODAY)


def run_main(*args: str) -> int:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(list(args))
    return excinfo.value.code


@respx.mock
def test_once_dry_run_prints_imax_screenings_and_leaves_state_untouched(tmp_path, capsys):
    respx.get(URL).respond(json=load_fixture("film_events_flora_2026-12-18.json"))
    state_file = tmp_path / "state" / "notified.json"

    code = run_main(
        "--once",
        "--dry-run",
        "--date-from",
        "18.12.2026",
        "--date-to",
        "18.12.2026",
        "--state-file",
        str(state_file),
    )

    out = capsys.readouterr().out
    assert code == 0
    for time, availability in (
        ("10:40", "32% available"),
        ("14:00", "25% available"),
        ("17:20", "20% available"),
        ("20:40", "20% available"),
    ):
        assert f"{time}  IMAX VOLVO  {availability}" in out
    assert "18:00" not in out  # Sál 01 is never listed
    assert "Fri 18.12.2026" in out
    assert "https://www.cinemacity.cz/cz/booking-router/launch/1052/267906?lang=en" in out
    assert not state_file.exists()
    assert not state_file.parent.exists()


@respx.mock
def test_once_not_on_sale_prints_nothing(tmp_path, capsys):
    for day in ("2026-12-27", "2026-12-28"):
        respx.get(f"{BASE_URL}/film-events/in-cinema/1052/at-date/{day}").respond(
            json=load_fixture("film_events_empty.json")
        )
    code = run_main("--once", "--dry-run", "--date-from", "27.12.2026", "--date-to", "28.12.2026")
    assert code == 0
    assert capsys.readouterr().out == ""


@respx.mock
def test_once_exit_code_1_when_every_day_fails(monkeypatch):
    monkeypatch.setattr(cli, "make_client", lambda s: CinemaCityClient(wait=wait_none()))
    respx.get(URL).respond(500)
    assert run_main("--once", "--dry-run", "--date-from", DAY, "--date-to", DAY) == 1


def test_config_error_exits_with_2(capsys):
    code = run_main("--date-from", "31.12.2026", "--date-to", "01.12.2026")
    assert code == 2
    assert "Configuration error" in capsys.readouterr().err


@respx.mock
def test_once_real_run_writes_state_and_does_not_repeat(tmp_path, monkeypatch, capsys):
    respx.get(URL).respond(json=load_fixture("film_events_flora_2026-12-18.json"))
    hook = respx.post("https://discord.com/api/webhooks/1/tok").respond(204)
    state_file = tmp_path / "notified.json"
    args = (
        "--once",
        "--date-from",
        DAY,
        "--date-to",
        DAY,
        "--state-file",
        str(state_file),
        "--discord-webhook-urls",
        "https://discord.com/api/webhooks/1/tok",
    )
    assert run_main(*args) == 0
    assert hook.call_count == 1
    assert state_file.exists()
    assert run_main(*args) == 0
    assert hook.call_count == 1


@respx.mock
def test_once_failing_notification_prints_error_and_does_not_terminate(tmp_path, capsys):
    respx.get(URL).respond(json=load_fixture("film_events_flora_2026-12-18.json"))
    hook = respx.post("https://discord.com/api/webhooks/1/tok").respond(500)
    state_file = tmp_path / "notified.json"
    code = run_main(
        "--once",
        "--date-from",
        DAY,
        "--date-to",
        DAY,
        "--state-file",
        str(state_file),
        "--discord-webhook-urls",
        "https://discord.com/api/webhooks/1/tok",
    )
    assert code == 0
    assert hook.call_count == 1
    err = capsys.readouterr().err
    assert "ERROR" in err
    assert "Notifier 'discord' failed" in err
    assert "/1/tok" not in err
    assert not state_file.exists()  # failed channel retries on the next run


@respx.mock
def test_list_films(capsys):
    respx.get(f"{BASE_URL}/film-events/in-cinema/1052/at-date/2026-12-15").respond(
        json=load_fixture("film_events_flora_2026-12-18.json")
    )
    for day in range(16, 21):
        respx.get(f"{BASE_URL}/film-events/in-cinema/1052/at-date/2026-12-{day}").respond(
            json=load_fixture("film_events_empty.json")
        )
    code = run_main("--list-films", "--date-from", "15.12.2026", "--date-to", "20.12.2026")
    assert code == 0
    assert "8105s2r  Dune: Part Three" in capsys.readouterr().out


@respx.mock
def test_list_films_falls_back_to_all_films(capsys):
    respx.get(URL).respond(json=load_fixture("film_events_empty.json"))
    respx.get(f"{BASE_URL}/films/until/{DAY}").respond(
        json={"body": {"films": [{"id": "x1", "name": "Some Film"}]}}
    )
    assert run_main("--list-films", "--date-from", DAY, "--date-to", DAY) == 0
    assert "x1  Some Film" in capsys.readouterr().out


def test_loop_exits_when_range_is_over(monkeypatch):
    monkeypatch.setattr(cli, "prague_today", lambda: date(2027, 1, 1))
    settings = make_settings()
    assert cli.run_loop(settings, [], threading.Event()) == 0


def test_loop_runs_then_stops_on_event(monkeypatch):
    stop = threading.Event()
    calls = []

    def fake_check(settings, notifiers):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("first run explodes; loop must survive")
        stop.set()

    monkeypatch.setattr(cli, "run_check", fake_check)
    monkeypatch.setattr(threading.Event, "wait", lambda self, timeout=None: None)
    assert cli.run_loop(make_settings(), [], stop) == 0
    assert len(calls) == 2
