"""
Settings and configuration models for the generic ingestion service.

The core idea: everything that is specific to one external API lives in a
SourceConfig (loaded from a YAML file). No source-specific code should ever
be needed — only a new YAML file.
"""
from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./ingestion.db"
    sources_dir: str = "sources"

    class Config:
        env_file = ".env"


settings = Settings()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class AuthType(str, Enum):
    NONE = "none"
    API_KEY_HEADER = "api_key_header"
    API_KEY_QUERY = "api_key_query"
    BEARER_TOKEN = "bearer_token"
    BASIC = "basic"


class AuthConfig(BaseModel):
    type: AuthType = AuthType.NONE
    # Name of the header or query param to place the credential in.
    param_name: Optional[str] = None
    # Name of the environment variable holding the secret value.
    env_var: Optional[str] = None
    # For BASIC auth: separate env vars for username/password.
    username_env_var: Optional[str] = None
    password_env_var: Optional[str] = None

    def resolve_secret(self, env_var_name: Optional[str]) -> str:
        if not env_var_name:
            raise ValueError("auth config references no env_var but a secret was requested")
        value = os.environ.get(env_var_name)
        if value is None:
            raise ValueError(
                f"Environment variable '{env_var_name}' is not set but is required by this "
                f"source's auth config."
            )
        return value


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class PaginationType(str, Enum):
    NONE = "none"
    PAGE_NUMBER = "page_number"
    OFFSET_LIMIT = "offset_limit"
    CURSOR_BODY = "cursor_body"       # next page URL/token found in the JSON body
    LINK_HEADER = "link_header"       # next page URL found in the HTTP Link header


class PaginationConfig(BaseModel):
    type: PaginationType = PaginationType.NONE

    # page_number style
    page_param: str = "page"
    start_page: int = 1
    size_param: Optional[str] = None
    page_size: Optional[int] = None
    # Path (jmespath) to a boolean/field in the response indicating more pages exist.
    has_more_path: Optional[str] = None

    # offset_limit style
    offset_param: str = "offset"
    limit_param: str = "limit"
    limit: int = 50

    # cursor_body style: jmespath path to the field containing the next page's
    # full URL (or a cursor/token to substitute into cursor_param).
    next_url_path: Optional[str] = None
    next_cursor_path: Optional[str] = None
    cursor_param: Optional[str] = None

    # Safety cap so a misbehaving API can't cause an infinite loop.
    max_pages: int = 500


# ---------------------------------------------------------------------------
# Request / Response shape
# ---------------------------------------------------------------------------

class RequestConfig(BaseModel):
    method: str = "GET"
    path: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 15.0


class ResponseConfig(BaseModel):
    # jmespath expression to locate the list of records within the response JSON.
    # Empty string means "the whole response body is the list".
    records_path: str = ""
    # jmespath expression used to derive a stable external id for each record,
    # relative to the record itself (e.g. "id" or "sku"). Used for upsert dedup.
    external_id_path: str = "id"


# ---------------------------------------------------------------------------
# Destination
# ---------------------------------------------------------------------------

class DestinationType(str, Enum):
    DATABASE = "database"
    S3 = "s3"  # not implemented yet; present to demonstrate extensibility


class DestinationConfig(BaseModel):
    type: DestinationType = DestinationType.DATABASE
    table: str = "raw_records"


# ---------------------------------------------------------------------------
# Top-level SourceConfig
# ---------------------------------------------------------------------------

class SourceConfig(BaseModel):
    name: str
    base_url: str
    auth: AuthConfig = Field(default_factory=AuthConfig)
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    request: RequestConfig = Field(default_factory=RequestConfig)
    response: ResponseConfig = Field(default_factory=ResponseConfig)
    destination: DestinationConfig = Field(default_factory=DestinationConfig)


def load_source_config(path: str | Path) -> SourceConfig:
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    return SourceConfig(**raw)


def load_all_source_configs(directory: str | Path = None) -> dict[str, SourceConfig]:
    directory = Path(directory or settings.sources_dir)
    configs: dict[str, SourceConfig] = {}
    if not directory.exists():
        return configs
    for file in sorted(directory.glob("*.yaml")):
        cfg = load_source_config(file)
        configs[cfg.name] = cfg
    return configs
