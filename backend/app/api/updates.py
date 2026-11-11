"""Latest GitHub release, with a cache and an API-rate-limit fallback.

The renderer used to call the unauthenticated GitHub API directly. That quota
is only 60 requests per hour per public IP, so a shared NAT or a few retries
turned update checks into HTTP 403. The backend now owns the request, caches a
successful result, and falls back to GitHub's HTML redirect (which is not part
of the API quota) when the API is unavailable.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from fastapi import APIRouter

from app.api.deps import TokenDep
from app.core.config import VERSION
from app.core.errors import AppError

router = APIRouter(tags=["updates"])

REPOSITORY = "ctianye/CodeSlice"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASE_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
RELEASE_TAG_PREFIX = f"https://github.com/{REPOSITORY}/releases/tag/"
ATOM_FEED = f"https://github.com/{REPOSITORY}/releases.atom"
# A tiny static file on a CDN. GitHub's HTML/Atom hosts can be slow or
# unreachable on some networks even when api.github.com is not; jsDelivr is a
# useful last resort and does not share the API quota.
CDN_VERSION = f"https://cdn.jsdelivr.net/gh/{REPOSITORY}@master/version.json?app={VERSION}"

CACHE_TTL_SECONDS = 30 * 60
STALE_TTL_SECONDS = 24 * 60 * 60
TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
TAG_URL_RE = re.compile(r"/releases/tag/([^/?#\"<]+)")

_cache: dict[str, Any] = {"at": 0.0, "payload": None}


def _headers() -> dict[str, str]:
    return {"User-Agent": f"CodeSlice/{VERSION}", "Accept": "application/vnd.github+json"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=True,
        headers=_headers(),
    )


def _payload(tag: str, *, body: str = "", assets: list[dict[str, str]] | None = None,
             prerelease: bool = False, draft: bool = False) -> dict[str, Any]:
    if not TAG_RE.match(tag):
        raise ValueError(f"unexpected release tag: {tag}")
    return {
        "tag_name": tag,
        "html_url": f"{RELEASE_TAG_PREFIX}{tag}",
        "body": body,
        "assets": assets or [],
        "prerelease": prerelease,
        "draft": draft,
    }


async def _fetch_api() -> dict[str, Any]:
    async with _client() as client:
        response = await client.get(RELEASE_API)
        response.raise_for_status()
        data = response.json()
    tag = data.get("tag_name")
    if not isinstance(tag, str):
        raise ValueError("GitHub response has no tag_name")
    html_url = data.get("html_url")
    if not isinstance(html_url, str) or not html_url.startswith(RELEASE_TAG_PREFIX):
        raise ValueError("GitHub response has an invalid release URL")
    assets: list[dict[str, str]] = []
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            assets.append({"name": name, "browser_download_url": url})
    return {
        "tag_name": tag,
        "html_url": html_url,
        "body": data.get("body") if isinstance(data.get("body"), str) else "",
        "assets": assets,
        "prerelease": bool(data.get("prerelease")),
        "draft": bool(data.get("draft")),
    }


async def _fetch_html_redirect() -> dict[str, Any]:
    async with _client() as client:
        response = await client.get(RELEASE_PAGE)
        response.raise_for_status()
    match = TAG_URL_RE.search(str(response.url))
    if not match:
        raise ValueError("GitHub did not redirect to a release tag")
    return _payload(match.group(1))


async def _fetch_cdn() -> dict[str, Any]:
    async with _client() as client:
        response = await client.get(CDN_VERSION)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise ValueError("version.json is not an object")
    tag = data.get("tag")
    if not isinstance(tag, str) and isinstance(data.get("version"), str):
        tag = f"v{data['version']}"
    if not isinstance(tag, str):
        raise ValueError("version.json has no tag")
    return _payload(tag)


async def _fetch_atom() -> dict[str, Any]:
    async with _client() as client:
        response = await client.get(ATOM_FEED)
        response.raise_for_status()
    match = TAG_URL_RE.search(response.text)
    if not match:
        raise ValueError("GitHub Atom feed has no release tag")
    return _payload(match.group(1))


async def _fetch_fallback() -> dict[str, Any]:
    # Prefer the static CDN file: it is small and works on networks where the
    # GitHub HTML host times out. HTML and Atom remain secondary fallbacks.
    try:
        return await _fetch_cdn()
    except Exception:
        pass
    try:
        return await _fetch_html_redirect()
    except Exception:
        return await _fetch_atom()


async def latest_release(_: TokenDep) -> dict[str, Any]:
    now = time.monotonic()
    cached = _cache.get("payload")
    age = now - float(_cache.get("at") or 0)
    if cached is not None and age < CACHE_TTL_SECONDS:
        return cached

    try:
        payload = await _fetch_api()
    except Exception:
        # A cached API response has the changelog and asset list, so prefer it
        # over the smaller HTML/Atom fallback even when it is stale.
        if cached is not None and age < STALE_TTL_SECONDS:
            return cached
        try:
            payload = await _fetch_fallback()
        except Exception as exc:  # network is down or GitHub is blocking us
            raise AppError("暂时无法检查 GitHub Release，请稍后重试。", status_code=502) from exc

    _cache["payload"] = payload
    _cache["at"] = now
    return payload


@router.get("/updates/latest")
async def get_latest_release(_: TokenDep) -> dict[str, Any]:
    return await latest_release(_)
