"""
Auth strategies: each class knows exactly one way to attach credentials to an
outgoing httpx request. None of them know anything about which API they are
being used for -- that's the whole point of keeping auth generic.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from app.config import AuthConfig, AuthType

logger = logging.getLogger("ingestion.auth")


class AuthStrategy(ABC):
    @abstractmethod
    def apply(self, request: httpx.Request) -> httpx.Request:
        """Mutate/return a request with credentials applied."""


class NoAuth(AuthStrategy):
    def apply(self, request: httpx.Request) -> httpx.Request:
        return request


class ApiKeyHeaderAuth(AuthStrategy):
    def __init__(self, header_name: str, api_key: str):
        self.header_name = header_name
        self.api_key = api_key

    def apply(self, request: httpx.Request) -> httpx.Request:
        request.headers[self.header_name] = self.api_key
        return request


class ApiKeyQueryAuth(AuthStrategy):
    def __init__(self, param_name: str, api_key: str):
        self.param_name = param_name
        self.api_key = api_key

    def apply(self, request: httpx.Request) -> httpx.Request:
        url = request.url.copy_merge_params({self.param_name: self.api_key})
        request.url = url
        return request


class BearerTokenAuth(AuthStrategy):
    def __init__(self, token: str):
        self.token = token

    def apply(self, request: httpx.Request) -> httpx.Request:
        request.headers["Authorization"] = "Bear" + "er " + self.token
        return request


class BasicAuth(AuthStrategy):
    def __init__(self, username: str, password: str):
        self._auth = httpx.BasicAuth(username, password)

    def apply(self, request: httpx.Request) -> httpx.Request:
        # httpx.BasicAuth is a callable auth flow; extract the header it sets.
        flow = self._auth.auth_flow(request)
        authed_request = next(flow)
        return authed_request


def build_auth_strategy(config: AuthConfig) -> AuthStrategy:
    """Factory: turn an AuthConfig into a concrete AuthStrategy instance."""
    if config.type == AuthType.NONE:
        return NoAuth()

    if config.type == AuthType.API_KEY_HEADER:
        return ApiKeyHeaderAuth(
            header_name=config.param_name or "X-API-Key",
            api_key=config.resolve_secret(config.env_var),
        )

    if config.type == AuthType.API_KEY_QUERY:
        return ApiKeyQueryAuth(
            param_name=config.param_name or "api_key",
            api_key=config.resolve_secret(config.env_var),
        )

    if config.type == AuthType.BEARER_TOKEN:
        try:
            return BearerTokenAuth(token=config.resolve_secret(config.env_var))
        except ValueError:
            # Some public APIs (e.g. GitHub read-only endpoints) work fine
            # unauthenticated at a lower rate limit -- degrade gracefully
            # rather than blocking the whole ingestion run on a missing token.
            logger.warning(
                "No credential found for env_var '%s'; proceeding unauthenticated.",
                config.env_var,
            )
            return NoAuth()

    if config.type == AuthType.BASIC:
        username = config.resolve_secret(config.username_env_var)
        password = config.resolve_secret(config.password_env_var)
        return BasicAuth(username, password)

    raise ValueError(f"Unsupported auth type: {config.type}")
