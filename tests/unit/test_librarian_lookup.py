"""The librarian's "source this part for me" path, which Faraday's part
inspector calls when a board carries an MPN the catalogue cannot resolve.

What is pinned here is the honesty of the answer, because the caller acts on
it: a part that is genuinely absent, a distributor that could not be reached,
and a part that would not schema-validate must be three different outcomes.
Collapsing the middle one into the first is the specific bug this module was
written to prevent — ``fetch_dk_product`` swallows transport errors into the
same ``None`` a real miss produces.

No network: a fake Digi-Key client returns synthetic products, and staging is
redirected into tmp_path.
"""

from __future__ import annotations

import json

import pytest

from heaviside.librarian.fetcher.auth import MissingCredentialError
from heaviside.librarian.fetcher.base import DistributorError
from heaviside.librarian.fetcher.lookup import MAX_MPN_LENGTH, lookup_part

_MPN = "IPB017N10N5LFATMA1"


def _mosfet(mpn: str = _MPN) -> dict:
    """A Digi-Key product the converter accepts — same parameter spellings as
    tests/unit/test_fetcher_convert.py, which is where the converter's real
    contract is pinned."""
    return {
        "ManufacturerPartNumber": mpn,
        "Manufacturer": {"Value": "Infineon Technologies"},
        "DigiKeyPartNumber": f"{mpn}-ND",
        "ProductStatus": "Active",
        "Family": {"Value": "Transistors - FETs, MOSFETs - Single"},
        "PrimaryDatasheet": "https://www.infineon.com/dgdl/ipb017n10n5.pdf",
        "Description": {"ProductDescription": "MOSFET N-CH 100V 180A TDSON8"},
        "Parameters": [
            {"Parameter": "Drain to Source Voltage (Vdss)", "Value": "100 V"},
            {"Parameter": "Rds On (Max) @ Id, Vgs", "Value": "1.7 mΩ"},
            {"Parameter": "Current - Continuous Drain (Id) @ 25°C", "Value": "180 A"},
            {"Parameter": "Vgs(th) (Max) @ Id", "Value": "3 V"},
            {"Parameter": "Output Capacitance (Coss) @ Vds, Vgs", "Value": "2100 pF"},
            {"Parameter": "Gate Charge (Qg) @ Vgs", "Value": "155 nC"},
            {"Parameter": "Supplier Device Package", "Value": "TDSON-8"},
        ],
    }


def _unreachable(msg: str = "token refresh refused") -> DistributorError:
    return DistributorError("digikey", 401, msg)


class _FakeDK:
    """Answers like Digi-Key. `search_raises` makes the API unreachable."""

    def __init__(self, product=None, *, search_raises: Exception | None = None):
        self._p = product
        self._raises = search_raises
        self.closed = False

    def get_product(self, mpn):
        raise RuntimeError("no detail endpoint on this account")

    def search(self, mpn, limit=10):
        if self._raises is not None:
            raise self._raises
        return {"Products": [self._p] if self._p else []}

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# the three outcomes
# ---------------------------------------------------------------------------


def test_a_found_part_comes_back_valid_and_staged(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "heaviside.librarian.tas.component_exists", lambda category, mpn: False
    )
    out = lookup_part(_MPN, "mosfet", client=_FakeDK(_mosfet()), staging_root=tmp_path)

    assert out["found"] is True
    assert out["category"] == "mosfets"
    # the envelope is the catalogue's own shape, so the caller renders it with
    # the code it already has for a catalogue record
    assert out["component"]["semiconductor"]["mosfet"]["manufacturerInfo"]["reference"] == _MPN
    # …and it records where it came from, rather than arriving anonymous
    prov = out["component"]["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"][
        "provenance"
    ]
    assert prov[0]["sourceName"] == "DigiKey"

    # staged, not applied: the file is on disk under the category, and nothing
    # claims it reached TAS
    staged = tmp_path / "mosfets" / f"digikey-{_MPN}.json"
    assert staged.exists() and out["stored"] == str(staged)
    payload = json.loads(staged.read_text())
    assert payload["source"] == "digikey" and payload["mpn"] == _MPN
    assert payload["raw_response"]["ManufacturerPartNumber"] == _MPN
    assert "staged for review" in out["storedReason"]


def test_a_part_the_distributor_does_not_have_is_a_miss_not_an_error(tmp_path):
    out = lookup_part("NO-SUCH-PART-9999", client=_FakeDK(None), staging_root=tmp_path)
    assert out["found"] is False
    assert "no part with exactly this number" in out["reason"]
    assert not list(tmp_path.rglob("*.json"))


def test_an_unreachable_distributor_raises_and_is_never_reported_as_absent(tmp_path):
    """The bug this module exists to prevent. `fetch_dk_product` turns a
    transport failure into the same None a real miss gives, so a caller would
    be told the part does not exist when nobody ever asked."""
    fake = _FakeDK(_mosfet(), search_raises=_unreachable())
    with pytest.raises(DistributorError):
        lookup_part(_MPN, client=fake, staging_root=tmp_path)
    assert not list(tmp_path.rglob("*.json"))


def test_missing_credentials_raise_rather_than_looking_like_a_miss(tmp_path, monkeypatch):
    def _no_creds():
        raise MissingCredentialError("DigiKeyClient requires both client_id and client_secret.")

    monkeypatch.setattr(
        "heaviside.librarian.fetcher.digikey.DigiKeyClient", lambda *a, **k: _no_creds()
    )
    with pytest.raises(MissingCredentialError):
        lookup_part(_MPN, staging_root=tmp_path)


def test_endpoint_maps_a_missing_credential_to_502_not_500(monkeypatch):
    """A MissingCredentialError is a CredentialError, a SIBLING of
    DistributorError rather than a subclass. An endpoint that caught only
    DistributorError turned an unconfigured key into an unexplained 500."""
    from heaviside.librarian.fetcher import lookup as lookup_mod

    def _no_creds(mpn, cat=None):
        raise MissingCredentialError("DigiKeyClient requires both client_id and client_secret.")

    monkeypatch.setattr(lookup_mod, "lookup_part", _no_creds)
    r = TestClient(app).post("/librarian/lookup", json={"mpn": _MPN})
    assert r.status_code == 502


# ---------------------------------------------------------------------------
# what the caller may and may not decide
# ---------------------------------------------------------------------------


def test_the_distributors_taxonomy_outranks_the_boards_category_guess(tmp_path, monkeypatch):
    """A board guesses the category from a refdes and a footprint. Letting a
    wrong guess pick the converter is how a part becomes a schema-valid
    something-it-is-not, so the distributor's own family wins."""
    monkeypatch.setattr(
        "heaviside.librarian.tas.component_exists", lambda category, mpn: False
    )
    out = lookup_part(_MPN, "capacitor", client=_FakeDK(_mosfet()), staging_root=tmp_path)
    assert out["found"] is True
    assert out["category"] == "mosfets"


def test_a_part_already_in_tas_is_reported_not_staged_twice(tmp_path, monkeypatch):
    monkeypatch.setattr("heaviside.librarian.tas.component_exists", lambda category, mpn: True)
    out = lookup_part(_MPN, client=_FakeDK(_mosfet()), staging_root=tmp_path)
    assert out["found"] is True
    assert out["stored"] is None
    assert "already in TAS" in out["storedReason"]
    assert not list(tmp_path.rglob("*.json"))


def test_a_part_that_will_not_validate_is_neither_returned_nor_staged(tmp_path, monkeypatch):
    """A half-parsed part is worse than no part: it would be rendered as a real
    datasheet and could justify a substitution on fabricated numbers."""
    from heaviside.librarian import tas as tas_mod

    monkeypatch.setattr(tas_mod, "component_exists", lambda category, mpn: False)

    def _reject(db_cat, component):
        raise tas_mod.ValidationError(
            db_cat, _MPN, [("electrical.drainSourceVoltage", "is a required property")]
        )

    monkeypatch.setattr(tas_mod, "validate_component", _reject)
    out = lookup_part(_MPN, client=_FakeDK(_mosfet()), staging_root=tmp_path)
    assert out["found"] is False
    assert "schema validation" in out["reason"]
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("bad", ["", "   ", "x" * (MAX_MPN_LENGTH + 1)])
def test_an_unusable_part_number_is_refused_before_any_distributor_call(bad, tmp_path):
    fake = _FakeDK(_mosfet(), search_raises=AssertionError("must not be called"))
    with pytest.raises(ValueError):
        lookup_part(bad, client=fake, staging_root=tmp_path)


def test_a_client_this_module_built_is_closed(tmp_path, monkeypatch):
    fake = _FakeDK(None)
    monkeypatch.setattr(
        "heaviside.librarian.fetcher.digikey.DigiKeyClient", lambda *a, **k: fake
    )
    lookup_part(_MPN, staging_root=tmp_path)
    assert fake.closed is True


# ---------------------------------------------------------------------------
# the HTTP surface
# ---------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from heaviside.api.server import app  # noqa: E402


def test_endpoint_returns_the_record_and_where_it_was_parked(tmp_path, monkeypatch):
    from heaviside.librarian.fetcher import lookup as lookup_mod

    monkeypatch.setattr(
        lookup_mod, "lookup_part", lambda mpn, cat=None: {"mpn": mpn, "found": True, "stored": "x"}
    )
    r = TestClient(app).post("/librarian/lookup", json={"mpn": _MPN})
    assert r.status_code == 200 and r.json()["found"] is True


def test_endpoint_says_502_when_the_distributor_cannot_be_asked(monkeypatch):
    from heaviside.librarian.fetcher import lookup as lookup_mod

    def _boom(mpn, cat=None):
        raise _unreachable("rate limited")

    monkeypatch.setattr(lookup_mod, "lookup_part", _boom)
    r = TestClient(app).post("/librarian/lookup", json={"mpn": _MPN})
    # NOT 200/found=false — the part was never looked up
    assert r.status_code == 502
    assert "could not reach" in r.json()["detail"]


def test_endpoint_refuses_an_empty_part_number(monkeypatch):
    r = TestClient(app).post("/librarian/lookup", json={"mpn": "  "})
    assert r.status_code == 422


def test_endpoint_is_key_gated_like_every_other_mutating_route(monkeypatch):
    """It spends a distributor call and writes a file, so it is a POST and the
    API-key middleware covers it — which is what lets nginx expose this one
    path publicly while the rest of the API stays unreachable."""
    monkeypatch.setenv("HEAVISIDE_API_KEY", "unit-test-secret")
    r = TestClient(app).post("/librarian/lookup", json={"mpn": _MPN})
    assert r.status_code == 401
