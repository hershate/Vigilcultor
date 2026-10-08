"""Scheduler tick tests (fake poller, credentials-missing path)."""

from __future__ import annotations

from vigilcultor.scheduler.loop import tick


class FakePoller:
    def __init__(self, result=None, raises: Exception | None = None):
        from vigilcultor.gh.poller import PollResult

        self._result = result or PollResult(
            installations_synced=2, issues_scanned=5, events_stored=1
        )
        self._raises = raises

    def poll_once(self):
        if self._raises:
            raise self._raises
        return self._result


def test_tick_summarizes_poll_result(tmp_env):
    summary = tick(FakePoller())
    assert summary == {"installations": 2, "projects": 0, "issues": 5, "events": 1}


def test_tick_survives_poller_crash(tmp_env):
    summary = tick(FakePoller(raises=RuntimeError("boom")))
    assert summary == {"error": "boom"}


def test_tick_without_credentials_skips(tmp_env, monkeypatch):
    from vigilcultor.gh import service

    monkeypatch.setattr(service, "build_app_client", lambda: None)
    assert tick() == {"skipped": "no_credentials"}
