"""
Extracts the actual list of records from an arbitrary JSON response shape,
using a jmespath expression supplied per-source in config. This is what
allows record_path="data.items" (fakeshop-style) and record_path="results"
(Rick and Morty-style) and record_path="" (a bare top-level array) to all be
handled by the same code.
"""
from __future__ import annotations

from typing import Any

import jmespath

from app.config import ResponseConfig


class ResponseExtractor:
    def __init__(self, config: ResponseConfig):
        self.config = config

    def extract_records(self, response_json: Any) -> list[dict]:
        if not self.config.records_path:
            records = response_json
        else:
            records = jmespath.search(self.config.records_path, response_json)

        if records is None:
            return []
        if not isinstance(records, list):
            raise ValueError(
                f"records_path '{self.config.records_path}' did not resolve to a list "
                f"(got {type(records).__name__})"
            )
        return records

    def extract_external_id(self, record: dict) -> str:
        value = jmespath.search(self.config.external_id_path, record)
        if value is None:
            raise ValueError(
                f"external_id_path '{self.config.external_id_path}' not found in record"
            )
        return str(value)
