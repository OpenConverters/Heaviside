"""Tests for ``heaviside.librarian.fetcher.mouser``.

Uses :class:`httpx.MockTransport` for deterministic responses.  No
network access.
"""

from __future__ import annotations

import json

import httpx
import pytest

from heaviside.librarian.fetcher.auth import MouserCredentials
from heaviside.librarian.fetcher.base import (
    DistributorError,
    MalformedResponseError,
    RateLimitError,
)
from heaviside.librarian.fetcher.mouser import MOUSER_API_BASE, MouserClient


def _creds() -> MouserCredentials:
    return MouserCredentials(api_key="test-key")


def _client(handler) -> MouserClient:
    return MouserClient(_creds(), transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_constructor_rejects_empty_api_key() -> None:
    with pytest.raises(ValueError, match="non-empty api_key"):
        MouserClient(
            MouserCredentials(api_key=""),
            transport=httpx.MockTransport(lambda _r: httpx.Response(200)),
        )


def test_default_base_url() -> None:
    assert MOUSER_API_BASE == "https://api.mouser.com/api/v1"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_happy_path_sends_expected_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/search/keyword"
        assert request.url.params["apiKey"] == "test-key"
        body = json.loads(request.content)
        skbr = body["SearchByKeywordRequest"]
        assert skbr["keyword"] == "MAX17574"
        assert skbr["records"] == 25
        assert skbr["startingRecord"] == 10
        return httpx.Response(
            200,
            json={
                "Errors": [],
                "SearchResults": {
                    "NumberOfResult": 1,
                    "Parts": [{"ManufacturerPartNumber": "MAX17574ATP+"}],
                },
            },
        )

    client = _client(handler)
    payload = client.search("MAX17574", limit=25, offset=10)
    assert payload["SearchResults"]["NumberOfResult"] == 1


def test_search_rejects_empty_keyword() -> None:
    client = _client(lambda _r: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="non-empty keyword"):
        client.search("")


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def test_429_raises_rate_limit_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="too many", headers={"Retry-After": "7"})

    with pytest.raises(RateLimitError) as excinfo:
        _client(handler).search("anything")
    assert excinfo.value.distributor == "mouser"
    assert excinfo.value.retry_after_seconds == 7.0


def test_500_raises_distributor_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(DistributorError) as excinfo:
        _client(handler).search("anything")
    assert excinfo.value.status_code == 500
    assert excinfo.value.distributor == "mouser"


def test_application_errors_in_200_body_are_promoted() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Errors": [{"Code": "Invalid", "Message": "bad api key"}],
                "SearchResults": None,
            },
        )

    with pytest.raises(DistributorError) as excinfo:
        _client(handler).search("x")
    assert excinfo.value.status_code == 200
    assert "bad api key" in str(excinfo.value)


def test_non_json_response_raises_malformed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>nope</html>")

    with pytest.raises(MalformedResponseError, match="non-JSON"):
        _client(handler).search("x")


def test_non_object_json_raises_malformed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected", "array"])

    with pytest.raises(MalformedResponseError, match="non-object"):
        _client(handler).search("x")


# ---------------------------------------------------------------------------
# get_product
# ---------------------------------------------------------------------------


def test_get_product_returns_exact_match_case_insensitive() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Errors": [],
                "SearchResults": {
                    "Parts": [
                        {"ManufacturerPartNumber": "Other-Part"},
                        {"ManufacturerPartNumber": "max17574atp+", "Manufacturer": "Maxim"},
                    ]
                },
            },
        )

    part = _client(handler).get_product("MAX17574ATP+")
    assert part is not None
    assert part["Manufacturer"] == "Maxim"


def test_get_product_returns_none_when_not_found() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Errors": [],
                "SearchResults": {"Parts": [{"ManufacturerPartNumber": "Different"}]},
            },
        )

    assert _client(handler).get_product("MAX17574ATP+") is None


def test_get_product_returns_none_when_parts_empty() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Errors": [], "SearchResults": {"Parts": []}})

    assert _client(handler).get_product("ANY") is None


def test_get_product_handles_missing_search_results() -> None:
    """A 200 with neither Errors nor SearchResults is "no match", not an error."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    assert _client(handler).get_product("ANY") is None


def test_get_product_rejects_empty_mpn() -> None:
    client = _client(lambda _r: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="non-empty MPN"):
        client.get_product("")


def test_get_product_propagates_distributor_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream")

    with pytest.raises(DistributorError):
        _client(handler).get_product("X")


def test_search_results_wrong_type_raises_malformed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"Errors": [], "SearchResults": "not-an-object"},
        )

    with pytest.raises(MalformedResponseError, match="SearchResults"):
        _client(handler).get_product("X")


def test_parts_wrong_type_raises_malformed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"Errors": [], "SearchResults": {"Parts": "not-a-list"}},
        )

    with pytest.raises(MalformedResponseError, match="Parts"):
        _client(handler).get_product("X")
