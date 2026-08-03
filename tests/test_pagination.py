"""Tests for pagination strategies: each must correctly compute whether
there's a next page and build the right request for it, and must always
respect max_pages to guard against infinite loops."""
import httpx
import pytest

from app.config import PaginationConfig, PaginationType
from app.pagination import (
    CursorBodyPagination,
    LinkHeaderPagination,
    NoPagination,
    OffsetLimitPagination,
    PageNumberPagination,
    _parse_next_link,
    build_pagination_strategy,
)


def make_request(url="https://example.com/data"):
    return httpx.Request("GET", url)


def test_no_pagination_never_has_next_page():
    strategy = NoPagination(PaginationConfig(type=PaginationType.NONE))
    req = strategy.initial_request(make_request())
    assert strategy.next_request(req, req, {}, httpx.Response(200), 0) is None


def test_page_number_pagination_increments_page():
    config = PaginationConfig(type=PaginationType.PAGE_NUMBER, page_param="page", start_page=1, max_pages=10)
    strategy = PageNumberPagination(config)
    base = make_request()
    first = strategy.initial_request(base)
    assert first.url.params["page"] == "1"

    second = strategy.next_request(base, first, {}, httpx.Response(200), 0)
    assert second.url.params["page"] == "2"


def test_page_number_pagination_respects_has_more_path():
    config = PaginationConfig(
        type=PaginationType.PAGE_NUMBER, has_more_path="has_more", max_pages=10
    )
    strategy = PageNumberPagination(config)
    base = make_request()
    first = strategy.initial_request(base)
    result = strategy.next_request(base, first, {"has_more": False}, httpx.Response(200), 0)
    assert result is None


def test_page_number_pagination_stops_at_max_pages():
    config = PaginationConfig(type=PaginationType.PAGE_NUMBER, max_pages=3)
    strategy = PageNumberPagination(config)
    base = make_request()
    first = strategy.initial_request(base)
    # page_index=2 means we've already fetched pages 0,1,2 -- at max_pages=3, stop.
    result = strategy.next_request(base, first, {}, httpx.Response(200), 2)
    assert result is None


def test_offset_limit_pagination_increments_offset():
    config = PaginationConfig(type=PaginationType.OFFSET_LIMIT, limit=50, max_pages=10)
    strategy = OffsetLimitPagination(config)
    base = make_request()
    first = strategy.initial_request(base)
    assert first.url.params["offset"] == "0"
    assert first.url.params["limit"] == "50"

    second = strategy.next_request(base, first, {}, httpx.Response(200), 0)
    assert second.url.params["offset"] == "50"


def test_cursor_body_pagination_follows_next_url():
    config = PaginationConfig(
        type=PaginationType.CURSOR_BODY, next_url_path="info.next", max_pages=10
    )
    strategy = CursorBodyPagination(config)
    base = make_request()
    first = strategy.initial_request(base)
    body = {"info": {"next": "https://example.com/data?page=2"}}
    second = strategy.next_request(base, first, body, httpx.Response(200), 0)
    assert str(second.url) == "https://example.com/data?page=2"


def test_cursor_body_pagination_stops_when_next_is_null():
    config = PaginationConfig(
        type=PaginationType.CURSOR_BODY, next_url_path="info.next", max_pages=10
    )
    strategy = CursorBodyPagination(config)
    base = make_request()
    first = strategy.initial_request(base)
    body = {"info": {"next": None}}
    result = strategy.next_request(base, first, body, httpx.Response(200), 0)
    assert result is None


def test_link_header_pagination_follows_rel_next():
    config = PaginationConfig(type=PaginationType.LINK_HEADER, max_pages=10)
    strategy = LinkHeaderPagination(config)
    base = make_request()
    first = strategy.initial_request(base)
    response = httpx.Response(
        200,
        headers={
            "Link": '<https://api.github.com/repos/x/issues?page=2>; rel="next", '
            '<https://api.github.com/repos/x/issues?page=5>; rel="last"'
        },
    )
    second = strategy.next_request(base, first, None, response, 0)
    assert str(second.url) == "https://api.github.com/repos/x/issues?page=2"


def test_link_header_pagination_stops_without_next_rel():
    config = PaginationConfig(type=PaginationType.LINK_HEADER, max_pages=10)
    strategy = LinkHeaderPagination(config)
    base = make_request()
    first = strategy.initial_request(base)
    response = httpx.Response(
        200,
        headers={"Link": '<https://api.github.com/repos/x/issues?page=1>; rel="prev"'},
    )
    result = strategy.next_request(base, first, None, response, 0)
    assert result is None


def test_parse_next_link_extracts_url():
    header = '<https://x.com/a?page=2>; rel="next", <https://x.com/a?page=1>; rel="prev"'
    assert _parse_next_link(header) == "https://x.com/a?page=2"


def test_parse_next_link_returns_none_when_absent():
    header = '<https://x.com/a?page=1>; rel="prev"'
    assert _parse_next_link(header) is None


def test_build_pagination_strategy_unknown_type_raises():
    config = PaginationConfig()
    config.type = "bogus"
    with pytest.raises(ValueError):
        build_pagination_strategy(config)
