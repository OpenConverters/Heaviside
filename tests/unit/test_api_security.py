"""API security guards: auth on mutating endpoints, SSRF on the URL fetch, and
a cap on the (LLM-token-burning) job queue.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from heaviside.api import jobs as jobs_mod
from heaviside.api.jobs import Job, JobQueueFull, JobRegistry
from heaviside.api.server import app

_KEY = "unit-test-secret"


def _client() -> TestClient:
    return TestClient(app)


def test_get_is_open_without_key(monkeypatch) -> None:
    monkeypatch.delenv("HEAVISIDE_API_KEY", raising=False)
    assert _client().get("/health").status_code == 200


def test_post_requires_key_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("HEAVISIDE_API_KEY", _KEY)
    c = _client()
    body = {"url": "https://example.com", "target_manufacturer": "Wurth"}
    assert c.post("/jobs/crossref/from-url", json=body).status_code == 401
    assert (
        c.post("/jobs/crossref/from-url", headers={"X-API-Key": "wrong"}, json=body).status_code
        == 401
    )


def test_post_open_when_key_unset(monkeypatch) -> None:
    """Backward-compatible: with no key configured, auth is not enforced (a
    loud startup warning covers this). SSRF still applies — use a bad URL so no
    real fetch/job runs, and assert we got PAST auth (400, not 401)."""
    monkeypatch.delenv("HEAVISIDE_API_KEY", raising=False)
    r = _client().post(
        "/jobs/crossref/from-url",
        json={"url": "http://127.0.0.1/", "target_manufacturer": "Wurth"},
    )
    assert r.status_code == 400  # reached the SSRF guard, not blocked by auth


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/", "http://localhost/", "http://10.1.2.3/"])
def test_ssrf_urls_rejected_with_400(monkeypatch, url) -> None:
    monkeypatch.setenv("HEAVISIDE_API_KEY", _KEY)
    r = _client().post(
        "/jobs/crossref/from-url",
        headers={"Authorization": f"Bearer {_KEY}"},
        json={"url": url, "target_manufacturer": "Wurth"},
    )
    assert r.status_code == 400
    assert "SSRF" in r.json()["detail"] or "non-public" in r.json()["detail"]


def test_job_queue_cap(monkeypatch, tmp_path) -> None:
    """submit() raises JobQueueFull once the in-flight cap is hit."""
    monkeypatch.setattr(jobs_mod, "_MAX_INFLIGHT_JOBS", 2)
    reg = JobRegistry(persist_dir=tmp_path)
    # Inject two jobs that stay 'queued' (never enqueued, so the worker can't
    # drain them) to simulate a full queue.
    with reg._lock:
        for i in range(2):
            reg._jobs[f"stuck{i}"] = Job(id=f"stuck{i}", kind="x", status="queued")
    with pytest.raises(JobQueueFull):
        reg.submit("crossref", lambda: None)
