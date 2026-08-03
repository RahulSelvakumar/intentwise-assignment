"""Tests for auth strategies: each strategy must attach the correct
credential in the correct place, and secrets must be pulled from env vars
(never hardcoded)."""
import httpx
import pytest

from app.auth import (
    ApiKeyHeaderAuth,
    ApiKeyQueryAuth,
    BasicAuth,
    BearerTokenAuth,
    NoAuth,
    build_auth_strategy,
)
from app.config import AuthConfig, AuthType


def make_request() -> httpx.Request:
    return httpx.Request("GET", "https://example.com/data")


def test_no_auth_leaves_request_untouched():
    request = make_request()
    result = NoAuth().apply(request)
    assert "Authorization" not in result.headers
    assert result is request


def test_api_key_header_auth_sets_header():
    request = make_request()
    result = ApiKeyHeaderAuth(header_name="X-API-Key", api_key="secret123").apply(request)
    assert result.headers["X-API-Key"] == "secret123"


def test_api_key_query_auth_sets_query_param():
    request = make_request()
    result = ApiKeyQueryAuth(param_name="key", api_key="secret123").apply(request)
    assert result.url.params["key"] == "secret123"


def test_bearer_token_auth_sets_authorization_header():
    request = make_request()
    result = BearerTokenAuth(token="abc123").apply(request)
    assert result.headers["Authorization"] == "Bearer abc123"


def test_basic_auth_sets_authorization_header():
    request = make_request()
    result = BasicAuth("user", "pass").apply(request)
    assert result.headers["Authorization"].startswith("Basic ")


def test_build_auth_strategy_none():
    strategy = build_auth_strategy(AuthConfig(type=AuthType.NONE))
    assert isinstance(strategy, NoAuth)


def test_build_auth_strategy_api_key_header_reads_env(monkeypatch):
    monkeypatch.setenv("MY_KEY", "value-from-env")
    config = AuthConfig(type=AuthType.API_KEY_HEADER, param_name="X-Key", env_var="MY_KEY")
    strategy = build_auth_strategy(config)
    assert isinstance(strategy, ApiKeyHeaderAuth)
    result = strategy.apply(make_request())
    assert result.headers["X-Key"] == "value-from-env"


def test_build_auth_strategy_bearer_missing_env_degrades_to_no_auth(monkeypatch, caplog):
    monkeypatch.delenv("MISSING_TOKEN_VAR", raising=False)
    config = AuthConfig(type=AuthType.BEARER_TOKEN, env_var="MISSING_TOKEN_VAR")
    strategy = build_auth_strategy(config)
    # Should degrade gracefully rather than raising -- some public APIs work
    # unauthenticated at a lower rate limit.
    assert isinstance(strategy, NoAuth)


def test_build_auth_strategy_api_key_header_missing_env_raises():
    config = AuthConfig(type=AuthType.API_KEY_HEADER, param_name="X-Key", env_var="DOES_NOT_EXIST_VAR")
    with pytest.raises(ValueError):
        build_auth_strategy(config)
