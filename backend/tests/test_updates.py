"""Update checks use the backend cache and survive GitHub API rate limits."""

from __future__ import annotations

import pytest

from app.api import updates


@pytest.fixture(autouse=True)
def _clear_update_cache():
    updates._cache.update({"at": 0.0, "payload": None})
    yield
    updates._cache.update({"at": 0.0, "payload": None})


def _release_payload() -> dict:
    return {
        "tag_name": "v0.2.4",
        "html_url": "https://github.com/ctianye/CodeSlice/releases/tag/v0.2.4",
        "body": "notes",
        "assets": [
            {
                "name": "CodeSlice-0.2.4-Setup.exe",
                "browser_download_url": "https://github.com/ctianye/CodeSlice/releases/download/v0.2.4/CodeSlice-0.2.4-Setup.exe",
            }
        ],
        "prerelease": False,
        "draft": False,
    }


def test_latest_release_returns_api_payload(client, monkeypatch):
    async def fake_api():
        return _release_payload()

    async def fail_fallback():
        raise AssertionError("fallback must not run when the API succeeds")

    monkeypatch.setattr(updates, "_fetch_api", fake_api)
    monkeypatch.setattr(updates, "_fetch_fallback", fail_fallback)

    response = client.get("/api/updates/latest")
    assert response.status_code == 200, response.text
    assert response.json()["tag_name"] == "v0.2.4"


def test_latest_release_falls_back_when_api_is_rate_limited(client, monkeypatch):
    async def rate_limited():
        raise RuntimeError("HTTP 403 rate limit exceeded")

    async def fallback():
        return updates._payload("v0.2.4")

    monkeypatch.setattr(updates, "_fetch_api", rate_limited)
    monkeypatch.setattr(updates, "_fetch_fallback", fallback)

    response = client.get("/api/updates/latest")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tag_name"] == "v0.2.4"
    assert body["html_url"].endswith("/releases/tag/v0.2.4")


def test_latest_release_is_cached(client, monkeypatch):
    calls = 0

    async def fake_api():
        nonlocal calls
        calls += 1
        return _release_payload()

    monkeypatch.setattr(updates, "_fetch_api", fake_api)
    monkeypatch.setattr(updates, "_fetch_fallback", lambda: None)

    assert client.get("/api/updates/latest").status_code == 200
    assert client.get("/api/updates/latest").status_code == 200
    assert calls == 1


def test_latest_release_requires_token(client, monkeypatch):
    async def fake_api():
        return _release_payload()

    monkeypatch.setattr(updates, "_fetch_api", fake_api)
    response = client.get("/api/updates/latest", headers={"X-Session-Token": ""})
    assert response.status_code == 401
