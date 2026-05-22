"""Unit tests for ``scripts/librarian_run.py``.

End-to-end wiring test using ``httpx.MockTransport`` — no network access,
no TAS writes. Exercises ``_process_mpn`` against a stubbed Mouser response
to confirm fetch -> convert -> stage works without touching the live
distributor APIs (which are intermittently rate-limited).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from heaviside.librarian.fetcher.auth import DigiKeyCredentials, MouserCredentials
from heaviside.librarian.fetcher.digikey import DigiKeyClient
from heaviside.librarian.fetcher.mouser import MouserClient


# Load scripts/librarian_run.py without it being a package member.
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "librarian_run.py"
_spec = importlib.util.spec_from_file_location("librarian_run", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
librarian_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(librarian_run)


_MOUSER_CAPACITOR = {
    "ManufacturerPartNumber": "TEST-CAP-001-DOES-NOT-EXIST",
    "Manufacturer": "Acme Test Caps",
    "MouserPartNumber": "TEST-MOUSER-PN",
    "Description": "TEST CAP CER 0.22UF 50V X7R 0603",
    "DataSheetUrl": "http://example.invalid/ds.pdf",
    "ProductDetailUrl": "http://example.invalid/pdp",
    "AvailabilityInStock": "5000",
    "PriceBreaks": [{"Quantity": 1, "Price": "$0.05", "Currency": "USD"}],
    "ProductAttributes": [
        {"AttributeName": "Capacitance", "AttributeValue": "0.22 µF"},
        {"AttributeName": "Voltage - Rated", "AttributeValue": "50 V"},
        {"AttributeName": "ESR (Equivalent Series Resistance)", "AttributeValue": "30 mΩ"},
        {"AttributeName": "Ripple Current @ Low Frequency", "AttributeValue": "1.5 A"},
        {"AttributeName": "Package / Case", "AttributeValue": "0603"},
        {"AttributeName": "Family", "AttributeValue": "Ceramic Capacitors"},
        {"AttributeName": "Series", "AttributeValue": "TEST"},
        {"AttributeName": "Mounting Type", "AttributeValue": "Surface Mount, MLCC"},
    ],
}


def _mouser_handler_returning_part() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/search/keyword"
        body = json.loads(request.content)
        kw = body["SearchByKeywordRequest"]["keyword"]
        assert kw == _MOUSER_CAPACITOR["ManufacturerPartNumber"]
        return httpx.Response(
            200,
            json={
                "Errors": [],
                "SearchResults": {
                    "NumberOfResult": 1,
                    "Parts": [_MOUSER_CAPACITOR],
                },
            },
        )
    return httpx.MockTransport(handler)


def _digikey_handler_rate_limited() -> httpx.MockTransport:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "60"}, text="rate limit")
    return httpx.MockTransport(handler)


def test_process_mpn_mouser_success_dry_run(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mouser returns a valid capacitor product; dry-run stages it without
    touching TAS."""
    # Retarget staging into tmp_path so nothing leaks into the repo.
    from heaviside.librarian.fetcher import staging
    monkeypatch.setattr(staging, "STAGING_DIR", Path(tmp_path) / "staging")  # type: ignore[arg-type]

    mouser = MouserClient(MouserCredentials(api_key="test"), transport=_mouser_handler_returning_part())
    digikey = DigiKeyClient(
        DigiKeyCredentials(client_id="x", client_secret="y", refresh_token="z"),
        transport=_digikey_handler_rate_limited(),
    )

    outcome, detail = librarian_run._process_mpn(
        _MOUSER_CAPACITOR["ManufacturerPartNumber"],
        category="capacitors",
        mouser=mouser,
        digikey=digikey,
        dry_run=True,
    )

    mouser.close()
    digikey.close()

    assert outcome == "staged", f"expected 'staged', got {outcome!r}: {detail}"
    staged_path = Path(detail)
    assert staged_path.exists(), f"staging file missing: {staged_path}"
    payload = json.loads(staged_path.read_text())
    assert payload["source"] == "mouser"
    assert payload["category"] == "capacitors"
    assert payload["mpn"] == _MOUSER_CAPACITOR["ManufacturerPartNumber"]


def test_process_mpn_mouser_miss_no_digikey_returns_miss(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mouser returns no matching part; Digi-Key is unavailable (None client).
    The runner reports a clean 'miss' rather than raising."""
    from heaviside.librarian.fetcher import staging
    monkeypatch.setattr(staging, "STAGING_DIR", Path(tmp_path) / "staging")  # type: ignore[arg-type]

    def mouser_empty(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "Errors": [], "SearchResults": {"NumberOfResult": 0, "Parts": []},
        })

    mouser = MouserClient(MouserCredentials(api_key="test"),
                          transport=httpx.MockTransport(mouser_empty))

    outcome, detail = librarian_run._process_mpn(
        "UNKNOWN-PART-XYZ-12345",
        category="capacitors",
        mouser=mouser, digikey=None, dry_run=True,
    )

    mouser.close()

    assert outcome == "miss", f"expected 'miss', got {outcome!r}: {detail}"


def test_process_mpn_skips_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A part already in TAS short-circuits without hitting any distributor."""
    monkeypatch.setattr(librarian_run, "component_exists", lambda _cat, _mpn: True)

    # Clients should never be touched; pass None to assert that.
    outcome, detail = librarian_run._process_mpn(
        "ANY-MPN",
        category="capacitors",
        mouser=None, digikey=None, dry_run=True,
    )

    assert outcome == "skipped_existing"
    assert "already in TAS" in detail
