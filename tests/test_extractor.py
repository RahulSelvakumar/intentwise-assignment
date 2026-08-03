"""Tests for ResponseExtractor: must correctly locate the record list and
a stable external id across differently-shaped API responses, and must
raise (not silently drop) on unrecoverable schema mismatches."""
import pytest

from app.config import ResponseConfig
from app.extractor import ResponseExtractor


def test_extract_records_top_level_array():
    extractor = ResponseExtractor(ResponseConfig(records_path="", external_id_path="id"))
    body = [{"id": 1}, {"id": 2}]
    assert extractor.extract_records(body) == body


def test_extract_records_nested_path():
    extractor = ResponseExtractor(ResponseConfig(records_path="results", external_id_path="id"))
    body = {"info": {"next": None}, "results": [{"id": 1}, {"id": 2}]}
    assert extractor.extract_records(body) == [{"id": 1}, {"id": 2}]


def test_extract_records_deeply_nested_path():
    extractor = ResponseExtractor(ResponseConfig(records_path="data.items", external_id_path="sku"))
    body = {"data": {"items": [{"sku": "A1"}]}}
    assert extractor.extract_records(body) == [{"sku": "A1"}]


def test_extract_records_missing_path_returns_empty_list():
    extractor = ResponseExtractor(ResponseConfig(records_path="does.not.exist", external_id_path="id"))
    body = {"results": [{"id": 1}]}
    assert extractor.extract_records(body) == []


def test_extract_records_non_list_raises():
    extractor = ResponseExtractor(ResponseConfig(records_path="result", external_id_path="id"))
    body = {"result": {"id": 1}}
    with pytest.raises(ValueError):
        extractor.extract_records(body)


def test_extract_external_id_returns_string():
    extractor = ResponseExtractor(ResponseConfig(external_id_path="id"))
    assert extractor.extract_external_id({"id": 42}) == "42"


def test_extract_external_id_nested_path():
    extractor = ResponseExtractor(ResponseConfig(external_id_path="meta.sku"))
    assert extractor.extract_external_id({"meta": {"sku": "ABC"}}) == "ABC"


def test_extract_external_id_missing_raises():
    extractor = ResponseExtractor(ResponseConfig(external_id_path="id"))
    with pytest.raises(ValueError):
        extractor.extract_external_id({"name": "no id here"})
