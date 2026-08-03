"""Tests for HttpFetcher: retries on 5xx/429/timeouts, does not retry on
other 4xx, honors Retry-After, and applies default headers (e.g. User-Agent)
even to manually-constructed requests (regression test for a real bug found
during integration testing against the GitHub API)."""
import httpx
import pytest
import respx

from app.fetcher import HttpFetcher, USER_AGENT


@pytest.mark.anyio
@respx.mock
async def test_fetcher_applies_default_user_agent_header():
    route = respx.get("https://example.com/data").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    fetcher = HttpFetcher()
    request = httpx.Request("GET", "https://example.com/data")
    response = await fetcher.fetch(request)
    await fetcher.aclose()

    assert response.status_code == 200
    assert route.calls.last.request.headers["User-Agent"] == USER_AGENT


@pytest.mark.anyio
@respx.mock
async def test_fetcher_does_not_overwrite_explicit_header():
    respx.get("https://example.com/data").mock(return_value=httpx.Response(200, json={}))
    fetcher = HttpFetcher()
    request = httpx.Request("GET", "https://example.com/data", headers={"Authorization": "Bearer xyz"})
    response = await fetcher.fetch(request)
    await fetcher.aclose()
    assert response.request.headers["Authorization"] == "Bearer xyz"


@pytest.mark.anyio
@respx.mock
async def test_fetcher_retries_on_5xx_then_succeeds():
    route = respx.get("https://example.com/flaky").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    fetcher = HttpFetcher()
    request = httpx.Request("GET", "https://example.com/flaky")
    response = await fetcher.fetch(request)
    await fetcher.aclose()

    assert response.status_code == 200
    assert route.call_count == 2


@pytest.mark.anyio
@respx.mock
async def test_fetcher_does_not_retry_on_404():
    route = respx.get("https://example.com/missing").mock(return_value=httpx.Response(404))
    fetcher = HttpFetcher()
    request = httpx.Request("GET", "https://example.com/missing")
    response = await fetcher.fetch(request)
    await fetcher.aclose()

    assert response.status_code == 404
    assert route.call_count == 1


@pytest.mark.anyio
@respx.mock
async def test_fetcher_retries_on_429_with_retry_after():
    route = respx.get("https://example.com/limited").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    fetcher = HttpFetcher()
    request = httpx.Request("GET", "https://example.com/limited")
    response = await fetcher.fetch(request)
    await fetcher.aclose()

    assert response.status_code == 200
    assert route.call_count == 2
