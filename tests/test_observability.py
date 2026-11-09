"""Request ids, error bodies, client error reports, version, deep health,
the worker heartbeat, the scheduled verify and the decoder selftest."""

import json
import logging
import time

from kist.config import get_settings


def test_every_response_carries_a_request_id(client):
    ok = client.get("/api/health")
    assert len(ok.headers["x-request-id"]) == 16
    unauth = client.get("/api/items", headers={"Authorization": ""})
    assert unauth.status_code == 401 and unauth.headers["x-request-id"]
    mine = client.get("/api/health", headers={"X-Request-ID": "swag-abc.123"})
    assert mine.headers["x-request-id"] == "swag-abc.123"
    junk = client.get("/api/health", headers={"X-Request-ID": "no spaces <allowed>"})
    assert junk.headers["x-request-id"] != "no spaces <allowed>"


def test_unhandled_errors_return_the_request_id(client, monkeypatch, caplog):
    from kist.services import library

    def boom(session):
        raise RuntimeError("the disk is on fire")

    monkeypatch.setattr(library, "stats", boom)
    with caplog.at_level(logging.ERROR, logger="kist.http"):
        res = client.get("/api/stats")
    assert res.status_code == 500
    body = res.json()
    assert body == {"detail": "Internal error", "request_id": res.headers["x-request-id"]}
    assert "content-security-policy" in res.headers
    assert any("the disk is on fire" in (r.exc_text or "") for r in caplog.records)


def test_client_errors_are_logged_and_rate_limited(client, monkeypatch, caplog):
    from kist.api import misc
    from kist.observability import RateLimiter

    monkeypatch.setattr(misc, "client_errors", RateLimiter(limit=2, window=60))
    report = {"message": "Cannot read properties of undefined", "stack": "at Grid",
              "url": "https://kist/all?item=x", "user_agent": "Safari", "kind": "render"}
    with caplog.at_level(logging.WARNING, logger="kist.client"):
        for _ in range(4):
            assert client.post("/api/client-errors", json=report).status_code == 204
    logged = [r for r in caplog.records if r.name == "kist.client"]
    assert len(logged) == 2
    assert "Cannot read properties" in logged[0].getMessage()
    assert client.post("/api/client-errors", json={"message": "x" * 2001}).status_code == 422
    assert client.post("/api/client-errors", json={"message": "x"},
                       headers={"Authorization": ""}).status_code == 401


def test_version(client, monkeypatch):
    from kist import __version__

    monkeypatch.setattr(get_settings(), "git_commit", "abc1234")
    assert client.get("/api/version").json() == {"version": __version__, "commit": "abc1234"}
    assert client.get("/api/version", headers={"Authorization": ""}).status_code == 401


def test_deep_health(client, upload, clean):
    from kist import cli
    from kist.services import health

    upload()
    res = client.get("/api/health/deep")
    assert res.status_code == 200
    body = res.json()
    checks = body["checks"]
    assert set(checks) >= {"db", "migrations", "worker", "library", "jobs", "previews", "verify"}
    assert checks["db"]["ok"] and checks["migrations"]["ok"]
    assert checks["migrations"]["current"] == checks["migrations"]["head"]
    assert checks["library"]["writable"] and checks["library"]["free_bytes"] > 0
    assert checks["jobs"] == {"ok": True, "failed": 0, "stuck": 0, "oldest_pending_age_s": None}
    assert checks["previews"]["failed_24h"] == 0
    assert checks["verify"]["last_run"] is None
    # no worker has beaten in this library yet
    assert checks["worker"]["heartbeat_age_s"] is None and body["ok"] is False
    assert "version" in body and "commit" in body

    health.heartbeat_path().parent.mkdir(parents=True, exist_ok=True)
    health.heartbeat_path().write_text("{}")
    cli.verify()
    body = client.get("/api/health/deep").json()
    assert body["ok"] is True
    assert body["checks"]["worker"]["heartbeat_age_s"] < 5
    assert body["checks"]["verify"]["drift"] == 0 and body["checks"]["verify"]["last_run"]
    assert client.get("/api/health/deep", headers={"Authorization": ""}).status_code == 401


def test_worker_heartbeat_needs_the_database_and_a_moving_loop(clean):
    from kist import worker
    from kist.services import health

    hb = worker.Heartbeat(stuck_after=60)
    assert hb.beat_once() is True
    assert worker.healthcheck() == 0
    assert json.loads(health.heartbeat_path().read_text())["pid"]
    # a loop that hasn't moved in longer than a job may take: no beat
    health.heartbeat_path().unlink()
    hb.progress = time.monotonic() - 120
    assert hb.beat_once() is False
    assert worker.healthcheck() == 1


def test_scheduled_verify(clean):
    from kist import worker
    from kist.services import verify

    (clean / "items").mkdir()
    assert worker.verify_due(24) is True
    worker.run_verify()
    last = verify.last_result()
    assert last["drift"] == 0 and last["items_checked"] == 0
    assert worker.verify_due(24) is False
    assert worker.verify_due(0) is False  # turned off


def test_selftest_runs_every_decoder(capsys):
    from kist import cli

    assert cli.selftest() == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    for name in ("png", "jpg", "gif", "webp", "pdf"):
        assert f"✓ {name}" in out


def test_selftest_fails_when_a_decoder_does(monkeypatch, capsys):
    from kist import cli
    from kist.services import thumbs

    def broken(*a, **k):
        raise RuntimeError("preview generation failed (exit 1): ...\nVipsError: no loader")

    monkeypatch.setattr(thumbs, "generate_previews_isolated", broken)
    assert cli.selftest() == 1
    assert "✗ png: VipsError: no loader" in capsys.readouterr().out
