"""
Pagination strategies: each class knows exactly one way to figure out "is
there a next page, and if so, what request fetches it?" Every strategy is
built with a hard max_pages cap to protect against APIs that misbehave and
would otherwise cause an infinite loop.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional
from urllib.parse import urljoin

import httpx
import jmespath

from app.config import PaginationConfig, PaginationType


class PaginationStrategy(ABC):
    def __init__(self, config: PaginationConfig):
        self.config = config
        self.max_pages = config.max_pages

    @abstractmethod
    def initial_request(self, base_request: httpx.Request) -> httpx.Request:
        """Return the first request to issue (may add page/offset params)."""

    @abstractmethod
    def next_request(
        self,
        base_request: httpx.Request,
        current_request: httpx.Request,
        response_json: Optional[dict],
        response: httpx.Response,
        page_index: int,
    ) -> Optional[httpx.Request]:
        """Return the request for the next page, or None if there is no more data."""


class NoPagination(PaginationStrategy):
    def initial_request(self, base_request: httpx.Request) -> httpx.Request:
        return base_request

    def next_request(self, base_request, current_request, response_json, response, page_index):
        return None


class PageNumberPagination(PaginationStrategy):
    """?page=1&per_page=50 style. Stops when has_more_path is falsy, or (if
    not configured) when a page returns zero records -- handled by the caller."""

    def initial_request(self, base_request: httpx.Request) -> httpx.Request:
        params = {self.config.page_param: str(self.config.start_page)}
        if self.config.size_param and self.config.page_size:
            params[self.config.size_param] = str(self.config.page_size)
        url = base_request.url.copy_merge_params(params)
        return httpx.Request(base_request.method, url, headers=base_request.headers)

    def next_request(self, base_request, current_request, response_json, response, page_index):
        if page_index + 1 >= self.max_pages:
            return None

        if self.config.has_more_path and response_json is not None:
            has_more = jmespath.search(self.config.has_more_path, response_json)
            if not has_more:
                return None

        current_page = int(current_request.url.params.get(self.config.page_param, self.config.start_page))
        params = dict(current_request.url.params)
        params[self.config.page_param] = str(current_page + 1)
        url = current_request.url.copy_merge_params(params)
        return httpx.Request(current_request.method, url, headers=current_request.headers)


class OffsetLimitPagination(PaginationStrategy):
    """?offset=0&limit=50 style. Stops when a page returns fewer records than
    the limit (handled by the orchestrator) or max_pages is hit."""

    def initial_request(self, base_request: httpx.Request) -> httpx.Request:
        params = {
            self.config.offset_param: "0",
            self.config.limit_param: str(self.config.limit),
        }
        url = base_request.url.copy_merge_params(params)
        return httpx.Request(base_request.method, url, headers=base_request.headers)

    def next_request(self, base_request, current_request, response_json, response, page_index):
        if page_index + 1 >= self.max_pages:
            return None
        current_offset = int(current_request.url.params.get(self.config.offset_param, 0))
        params = dict(current_request.url.params)
        params[self.config.offset_param] = str(current_offset + self.config.limit)
        url = current_request.url.copy_merge_params(params)
        return httpx.Request(current_request.method, url, headers=current_request.headers)


class CursorBodyPagination(PaginationStrategy):
    """Next page info is inside the JSON response body -- either a full URL
    (next_url_path, e.g. Rick and Morty's info.next) or a cursor/token to be
    substituted into cursor_param on the next request."""

    def initial_request(self, base_request: httpx.Request) -> httpx.Request:
        return base_request

    def next_request(self, base_request, current_request, response_json, response, page_index):
        if page_index + 1 >= self.max_pages or response_json is None:
            return None

        if self.config.next_url_path:
            next_url = jmespath.search(self.config.next_url_path, response_json)
            if not next_url:
                return None
            return httpx.Request(current_request.method, next_url, headers=current_request.headers)

        if self.config.next_cursor_path and self.config.cursor_param:
            cursor = jmespath.search(self.config.next_cursor_path, response_json)
            if not cursor:
                return None
            params = dict(current_request.url.params)
            params[self.config.cursor_param] = str(cursor)
            url = current_request.url.copy_merge_params(params)
            return httpx.Request(current_request.method, url, headers=current_request.headers)

        return None


class LinkHeaderPagination(PaginationStrategy):
    """Next page URL is provided via the standard HTTP Link header with
    rel="next" (GitHub API style)."""

    def initial_request(self, base_request: httpx.Request) -> httpx.Request:
        return base_request

    def next_request(self, base_request, current_request, response_json, response, page_index):
        if page_index + 1 >= self.max_pages:
            return None
        link_header = response.headers.get("Link") or response.headers.get("link")
        if not link_header:
            return None
        next_url = _parse_next_link(link_header)
        if not next_url:
            return None
        return httpx.Request(current_request.method, next_url, headers=current_request.headers)


def _parse_next_link(link_header: str) -> Optional[str]:
    """Parse RFC5988 Link header value and return the rel="next" URL, if any."""
    for part in link_header.split(","):
        segments = part.strip().split(";")
        if len(segments) < 2:
            continue
        url_part = segments[0].strip()
        if not (url_part.startswith("<") and url_part.endswith(">")):
            continue
        rel_parts = [seg.strip() for seg in segments[1:]]
        if any(seg == 'rel="next"' or seg == "rel=next" for seg in rel_parts):
            return url_part[1:-1]
    return None


def build_pagination_strategy(config: PaginationConfig) -> PaginationStrategy:
    strategies = {
        PaginationType.NONE: NoPagination,
        PaginationType.PAGE_NUMBER: PageNumberPagination,
        PaginationType.OFFSET_LIMIT: OffsetLimitPagination,
        PaginationType.CURSOR_BODY: CursorBodyPagination,
        PaginationType.LINK_HEADER: LinkHeaderPagination,
    }
    cls = strategies.get(config.type)
    if cls is None:
        raise ValueError(f"Unsupported pagination type: {config.type}")
    return cls(config)
