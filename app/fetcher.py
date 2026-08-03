"""
HttpFetcher: the single place where we deal with the realities of calling
live, external, production APIs -- retries, backoff, timeouts, rate limiting,
and good API citizenship (custom User-Agent). Every source benefits from this
without any per-source code.
"""
from __future__ import annotations

import asyncio
import logging
from email.utils import parsedate_to_datetime
from time import time
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger("ingestion.fetcher")

USER_AGENT = "intentwise-ingestion-service/1.0 (+https://github.com/hrintentwise)"


class RetryableHttpError(Exception):
    """Raised for responses we consider worth retrying (5xx, 429)."""

    def __init__(self, response: httpx.Response):
        self.response = response
        super().__init__(f"Retryable HTTP error: {response.status_code}")


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError, RetryableHttpError))


class HttpFetcher:
    """Wraps an httpx.AsyncClient with retry/backoff, rate-limit awareness,
    and per-source concurrency limiting."""

    def __init__(self, max_concurrency: int = 4, default_timeout: float = 15.0):
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(default_timeout, connect=10.0),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def aclose(self):
        await self._client.aclose()

    async def fetch(self, request: httpx.Request) -> httpx.Response:
        # httpx.Client.send() does NOT merge the client's default headers
        # (e.g. our User-Agent) into a manually-constructed httpx.Request --
        # that only happens for requests built via client.build_request().
        # Apply them here so every request is a good API citizen, without
        # clobbering headers a source/auth strategy already set explicitly.
        for key, value in self._client.headers.items():
            request.headers.setdefault(key, value)

        async with self._semaphore:
            return await self._fetch_with_retry(request)

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential_jitter(initial=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _fetch_with_retry(self, request: httpx.Request) -> httpx.Response:
        response = await self._client.send(request)

        if response.status_code == 429:
            wait_seconds = _retry_after_seconds(response)
            logger.warning(
                "Rate limited by %s, sleeping %.1fs before retry", request.url.host, wait_seconds
            )
            await asyncio.sleep(wait_seconds)
            raise RetryableHttpError(response)

        if response.status_code >= 500:
            raise RetryableHttpError(response)

        if response.status_code == 401:
            # Not retryable: bad/expired credentials. Surface clearly rather
            # than retrying forever against a request that can never succeed.
            logger.error("Auth failed (401) for %s -- check credentials/env vars", request.url.host)

        # Respect proactive rate-limit headers even on success (e.g. GitHub's
        # X-RateLimit-Remaining) so we slow down before we get 429'd.
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset_at = response.headers.get("X-RateLimit-Reset")
        if remaining is not None and reset_at is not None:
            try:
                if int(remaining) <= 1:
                    sleep_for = max(0.0, float(reset_at) - time())
                    if sleep_for > 0:
                        logger.info("Near rate limit for %s, sleeping %.1fs", request.url.host, sleep_for)
                        await asyncio.sleep(min(sleep_for, 60.0))
            except ValueError:
                pass

        return response


def _retry_after_seconds(response: httpx.Response) -> float:
    retry_after = response.headers.get("Retry-After")
    if not retry_after:
        return 5.0
    try:
        return float(retry_after)
    except ValueError:
        try:
            dt = parsedate_to_datetime(retry_after)
            return max(0.0, dt.timestamp() - time())
        except Exception:
            return 5.0
