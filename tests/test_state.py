import json
import os

import pytest

from imax_checker import state
from imax_checker.checker import CheckResult
from imax_checker.notify import dispatch

from .conftest import make_screening


class FakeNotifier:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.calls: list[tuple[list[str], set[str]]] = []

    def send(self, screenings, new_ids, context) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append(([s.event_id for s in screenings], set(new_ids)))


def result_with(*ids: str) -> CheckResult:
    return CheckResult(bookable=[make_screening(i) for i in ids])


@pytest.fixture
def path(tmp_path):
    return tmp_path / ".state" / "notified.json"


def test_not_on_sale_sends_nothing(path, context):
    n = FakeNotifier("email")
    assert dispatch(CheckResult(), [n], context, path) == []
    assert n.calls == []
    assert not path.exists()


def test_first_on_sale_run_notifies_with_all_screenings(path, context):
    n = FakeNotifier("email")
    assert dispatch(result_with("1", "2"), [n], context, path) == ["email"]
    assert n.calls == [(["1", "2"], {"1", "2"})]
    assert json.loads(path.read_text()) == {"email": ["1", "2"]}


def test_same_data_twice_sends_once(path, context):
    n = FakeNotifier("email")
    dispatch(result_with("1", "2"), [n], context, path)
    assert dispatch(result_with("1", "2"), [n], context, path) == []
    assert len(n.calls) == 1


def test_new_screening_sends_followup_listing_everything_with_only_new_marked(path, context):
    n = FakeNotifier("discord")
    dispatch(result_with("1", "2"), [n], context, path)
    dispatch(result_with("1", "2", "3"), [n], context, path)
    assert n.calls[1] == (["1", "2", "3"], {"3"})
    assert state.load(path) == {"discord": {"1", "2", "3"}}


def test_failed_channel_retries_alone_next_run(path, context):
    email, discord = FakeNotifier("email", fail=True), FakeNotifier("discord")
    assert dispatch(result_with("1"), [email, discord], context, path) == ["discord"]
    assert state.load(path) == {"discord": {"1"}}

    email.fail = False
    assert dispatch(result_with("1"), [email, discord], context, path) == ["email"]
    assert len(discord.calls) == 1
    assert len(email.calls) == 1
    assert state.load(path) == {"discord": {"1"}, "email": {"1"}}


def test_any_notifier_exception_is_logged_and_the_run_continues(path, context, caplog):
    class Exploding(FakeNotifier):
        def send(self, screenings, new_ids, context) -> None:
            raise KeyError("unexpected")

    email, discord = Exploding("email"), FakeNotifier("discord")
    assert dispatch(result_with("1"), [email, discord], context, path) == ["discord"]
    assert discord.calls == [(["1"], {"1"})]
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "'email' failed" in errors[0].getMessage()


def test_state_save_failure_is_logged_and_other_channels_still_notified(
    path, context, monkeypatch, caplog
):
    def failing_save(path, state):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(state, "save", failing_save)
    email, discord = FakeNotifier("email"), FakeNotifier("discord")
    assert dispatch(result_with("1"), [email, discord], context, path) == ["email", "discord"]
    assert len(email.calls) == len(discord.calls) == 1
    assert "Could not save state file" in caplog.text


def test_disappearing_and_reappearing_id_does_not_renotify(path, context):
    n = FakeNotifier("email")
    dispatch(result_with("1", "2"), [n], context, path)
    dispatch(result_with("1"), [n], context, path)  # "2" disappeared
    assert state.load(path) == {"email": {"1", "2"}}  # never removed
    assert dispatch(result_with("1", "2"), [n], context, path) == []
    assert len(n.calls) == 1


def test_persist_false_neither_reads_nor_writes_state(path, context):
    state.save(path, {"stdout": {"1"}})
    before = path.read_text()
    n = FakeNotifier("stdout")
    assert dispatch(result_with("1"), [n], context, path, persist=False) == ["stdout"]
    assert path.read_text() == before
    assert n.calls == [(["1"], {"1"})]  # existing state is ignored in dry-run


def test_load_missing_and_corrupt_files(path):
    assert state.load(path) == {}
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert state.load(path) == {}
    path.write_text("[1, 2]")
    assert state.load(path) == {}


def test_save_creates_parent_dir_and_leaves_no_temp_files(path):
    state.save(path, {"email": {"b", "a"}})
    assert json.loads(path.read_text()) == {"email": ["a", "b"]}
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_save_is_atomic(path, monkeypatch):
    state.save(path, {"email": {"1"}})

    def failing_replace(src, dst):
        raise OSError("disk on fire")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError):
        state.save(path, {"email": {"1", "2"}})

    monkeypatch.undo()
    assert json.loads(path.read_text()) == {"email": ["1"]}  # old content intact
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # temp file cleaned up


def test_save_uses_os_replace(path, monkeypatch):
    calls = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda s, d: (calls.append((s, d)), real(s, d)))
    state.save(path, {"email": {"1"}})
    assert len(calls) == 1
    assert os.path.dirname(calls[0][0]) == os.path.dirname(calls[0][1])
