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
from heaviside.librarian.fetcher.lookup import MAX_MPN_LENGTH, _mouser_exact, lookup_part

_REAL_MOUSER_EXACT = _mouser_exact

_MPN = "IPB017N10N5LFATMA1"


@pytest.fixture
def real_mouser(monkeypatch):
    """Undo the autouse stub so a test can exercise _mouser_exact's own guard.
    Patching _mouser_exact from a test would only prove the mock returns what
    the mock was told to."""
    from heaviside.librarian.fetcher import lookup as lookup_mod

    monkeypatch.setattr(lookup_mod, "_mouser_exact", _REAL_MOUSER_EXACT)


@pytest.fixture(autouse=True)
def _no_secondary_sources(monkeypatch):
    """Digi-Key is faked per test; the two LATER sources must never touch the
    network from a unit test. Each is silenced explicitly rather than by a
    blanket socket block, so a test that means to exercise one just overrides
    it back."""
    monkeypatch.setattr(
        "heaviside.librarian.fetcher.lookup._mouser_exact",
        lambda mpn: (None, "no Mouser credentials are configured"))
    monkeypatch.setattr(
        "heaviside.librarian.fetcher.from_datasheet.envelope_from_datasheet",
        lambda *a, **k: (None, "the datasheet route is disabled in this test", {}))


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
    # each source's own answer is kept, so a miss says which of them said what
    dk = [a for a in out["attempts"] if a["source"] == "digikey"]
    assert dk and "no part with exactly this number" in dk[0]["outcome"]
    assert not list(tmp_path.rglob("*.json"))


def test_every_source_that_was_tried_is_named_in_the_answer(tmp_path):
    """A bare "not found" invites the reader to assume the whole web was
    searched. The answer lists what each source actually said."""
    out = lookup_part("NO-SUCH-PART-9999", "mosfet",
                      client=_FakeDK(None), staging_root=tmp_path)
    sources = [a["source"] for a in out["attempts"]]
    assert sources == ["digikey", "mouser", "datasheet"]
    assert all(a["outcome"] for a in out["attempts"])


def test_an_unreachable_distributor_raises_and_is_never_reported_as_absent(tmp_path):
    """The bug this module exists to prevent. `fetch_dk_product` turns a
    transport failure into the same None a real miss gives, so a caller would
    be told the part does not exist when nobody ever asked."""
    fake = _FakeDK(_mosfet(), search_raises=_unreachable())
    with pytest.raises(DistributorError):
        lookup_part(_MPN, client=fake, staging_root=tmp_path)
    assert not list(tmp_path.rglob("*.json"))


def test_missing_credentials_raise_rather_than_looking_like_a_miss(tmp_path, monkeypatch):
    """Builds the REAL client, on purpose.

    The first version of this test stubbed DigiKeyClient itself, so the only
    line that constructs one was never executed — and it was wrong:
    `DigiKeyClient()` takes a required `credentials` argument, so every live
    call raised TypeError and became a 500. Prod found that, not the suite.
    Point the credential lookup at an empty environment and let the genuine
    constructor run.
    """
    monkeypatch.setattr(
        "heaviside.librarian.fetcher.auth.CREDENTIALS_PATH", tmp_path / "nope.json"
    )
    for var in ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(MissingCredentialError):
        lookup_part(_MPN, staging_root=tmp_path)


def test_the_real_client_is_constructed_the_way_its_signature_demands(tmp_path, monkeypatch):
    """The constructor takes credentials positionally. Calling it bare is a
    TypeError, which is neither a miss nor a distributor failure — it is a 500,
    and no amount of stubbing the class would show it."""
    import inspect

    from heaviside.librarian.fetcher.digikey import DigiKeyClient

    params = list(inspect.signature(DigiKeyClient.__init__).parameters)
    assert params[1] == "credentials", "the lookup passes credentials positionally"

    built = {}
    creds = type("C", (), {"digikey": object()})()
    monkeypatch.setattr(
        "heaviside.librarian.fetcher.auth.load_credentials", lambda **k: creds
    )

    class _Spy(_FakeDK):
        def __init__(self, credentials, **kw):
            super().__init__(None)
            built["creds"] = credentials

    monkeypatch.setattr("heaviside.librarian.fetcher.digikey.DigiKeyClient", _Spy)
    out = lookup_part(_MPN, staging_root=tmp_path)
    assert built["creds"] is creds.digikey
    assert out["found"] is False


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
    dk = [a for a in out["attempts"] if a["source"] == "digikey"]
    assert dk and "schema validation" in dk[0]["outcome"]
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


# ---------------------------------------------------------------------------
# the second and third sources
# ---------------------------------------------------------------------------


def _mouser_row(mpn=_MPN):
    return {
        "ManufacturerPartNumber": mpn,
        "Manufacturer": "Infineon Technologies",
        "Category": "MOSFET",
        "DataSheetUrl": "https://www.infineon.com/dgdl/x.pdf",
        "ProductDetailUrl": "https://www.mouser.com/x",
    }


def test_mouser_is_asked_when_digikey_does_not_have_the_part(tmp_path, monkeypatch):
    """Mouser was configured and unused. It holds parts Digi-Key does not, and
    asking costs nothing that is not already paid for."""
    seen = {}
    env = {"semiconductor": {"mosfet": {"manufacturerInfo": {"reference": _MPN}}}}
    monkeypatch.setattr("heaviside.librarian.fetcher.lookup._mouser_exact",
                        lambda mpn: (seen.setdefault("asked", mpn) and None) or (_mouser_row(), ""))
    monkeypatch.setattr("heaviside.librarian.fetcher.lookup._mouser_envelope",
                        lambda p, mpn, hint: (env, "mosfets"))
    monkeypatch.setattr("heaviside.librarian.tas.component_exists", lambda c, m: False)
    out = lookup_part(_MPN, client=_FakeDK(None), staging_root=tmp_path)
    assert seen["asked"] == _MPN
    assert out["found"] is True and out["source"] == "mouser"
    assert (tmp_path / "mosfets" / f"mouser-{_MPN}.json").exists()


def test_a_rate_limited_mouser_is_not_reported_as_not_having_the_part(
        tmp_path, monkeypatch, real_mouser):
    """Mouser's key is rate-limited in practice. "Could not be asked" and "does
    not have it" are different facts and the trail keeps them apart."""
    from heaviside.librarian.fetcher.base import RateLimitError

    class _RateLimited:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def get_product(self, mpn):
            raise RateLimitError("mouser", 429, "")

    monkeypatch.setattr("heaviside.librarian.fetcher.mouser.MouserClient", _RateLimited)
    out = lookup_part(_MPN, "mosfet", client=_FakeDK(None), staging_root=tmp_path)
    mo = [a for a in out["attempts"] if a["source"] == "mouser"][0]
    assert "could not be asked" in mo["outcome"] or "credentials" in mo["outcome"]
    assert "no part with exactly this number" not in mo["outcome"]


def test_a_secondary_source_blowing_up_never_sinks_the_lookup(
        tmp_path, monkeypatch, real_mouser):
    """Digi-Key has already answered by the time Mouser is asked, so a flaky
    secondary must degrade to a note rather than an exception the caller sees.
    The guard lives inside _mouser_exact, so the failure is injected BELOW it —
    patching _mouser_exact itself would test the mock, not the guard."""

    class _Exploding:
        def __init__(self, *a, **k):
            raise RuntimeError("mouser transport exploded")

    monkeypatch.setattr("heaviside.librarian.fetcher.mouser.MouserClient", _Exploding)
    out = lookup_part(_MPN, "mosfet", client=_FakeDK(None), staging_root=tmp_path)
    assert out["found"] is False          # no crash reached the caller
    mo = [a for a in out["attempts"] if a["source"] == "mouser"][0]
    assert "could not be asked" in mo["outcome"]
    assert "RuntimeError" in mo["outcome"]


def test_the_datasheet_route_runs_when_no_distributor_has_the_part(tmp_path, monkeypatch):
    """The case that prompted this: IPA045N10N3G is a real Infineon MOSFET that
    Digi-Key does not list."""
    env = {"semiconductor": {"mosfet": {"manufacturerInfo": {"reference": "IPA045N10N3G"}}}}
    called = {}

    def _from_ds(mpn, category, *, manufacturer="", datasheet_url=None, **kw):
        called.update(mpn=mpn, category=category, url=datasheet_url)
        return env, "mosfets", {"read": "https://www.infineon.com/real.pdf"}

    monkeypatch.setattr(
        "heaviside.librarian.fetcher.from_datasheet.envelope_from_datasheet", _from_ds)
    monkeypatch.setattr("heaviside.librarian.tas.component_exists", lambda c, m: False)
    out = lookup_part("IPA045N10N3G", "mosfet", client=_FakeDK(None), staging_root=tmp_path)
    assert called["category"] == "mosfet"
    assert out["found"] is True and out["source"] == "datasheet"
    # the record names the document it was read from, so it can be checked
    assert out["readFrom"] == "https://www.infineon.com/real.pdf"
    assert (tmp_path / "mosfets" / "datasheet-IPA045N10N3G.json").exists()


def test_a_part_digikey_has_but_cannot_describe_reuses_its_datasheet_link(tmp_path, monkeypatch):
    """Digi-Key rarely publishes Coss, so the MOSFET converter refuses real
    parts over a number the datasheet prints. The route should read THAT
    datasheet rather than start a fresh web search."""
    product = _mosfet()
    product["Parameters"] = [p for p in product["Parameters"]
                             if "Output Capacitance" not in p["Parameter"]]
    product["PrimaryDatasheet"] = "https://www.infineon.com/dgdl/known.pdf"
    seen = {}

    def _from_ds(mpn, category, *, manufacturer="", datasheet_url=None, **kw):
        seen.update(url=datasheet_url, mfr=manufacturer)
        return None, "stub", {}

    monkeypatch.setattr(
        "heaviside.librarian.fetcher.from_datasheet.envelope_from_datasheet", _from_ds)
    lookup_part(_MPN, client=_FakeDK(product), staging_root=tmp_path)
    assert seen["url"] == "https://www.infineon.com/dgdl/known.pdf"
    assert seen["mfr"] == "Infineon Technologies"


def test_the_datasheet_route_is_refused_by_name_for_a_category_it_cannot_map(tmp_path):
    """A magnetic's saturation current is defined by the inductance drop it is
    quoted at, and a connector is described by a pinout, not a parameter list.
    Neither maps to a record without inventing something, so both are refused
    by name rather than attempted."""
    out = lookup_part("SOME-INDUCTOR-123", "magnetic",
                      client=_FakeDK(None), staging_root=tmp_path)
    ds = [a for a in out["attempts"] if a["source"] == "datasheet"][0]
    assert "only supported for" in ds["outcome"] and "mosfet" in ds["outcome"]
    assert not list(tmp_path.rglob("*.json"))


def test_nothing_is_read_from_the_web_when_the_caller_forbids_it(tmp_path, monkeypatch):
    def _must_not_run(*a, **k):
        raise AssertionError("the datasheet route ran with allow_datasheet=False")

    monkeypatch.setattr(
        "heaviside.librarian.fetcher.from_datasheet.envelope_from_datasheet", _must_not_run)
    out = lookup_part(_MPN, "mosfet", client=_FakeDK(None),
                      staging_root=tmp_path, allow_datasheet=False)
    assert out["found"] is False


def test_the_cli_builds_its_clients_the_way_their_signatures_demand():
    """`heaviside librarian search` had the identical bare-constructor bug the
    lookup module had, and shipped with it for as long. A signature check is
    cheap and catches the whole class."""
    import ast
    import inspect
    from pathlib import Path

    from heaviside.librarian.fetcher.digikey import DigiKeyClient
    from heaviside.librarian.fetcher.mouser import MouserClient

    for cls in (DigiKeyClient, MouserClient):
        params = list(inspect.signature(cls.__init__).parameters)
        assert params[1] == "credentials", f"{cls.__name__} takes credentials first"

    tree = ast.parse(Path("heaviside/cli.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("DigiKeyClient", "MouserClient")):
            assert node.args, (
                f"{node.func.id}() is constructed with no credentials at "
                f"heaviside/cli.py:{node.lineno} — that is a TypeError at runtime")


def test_mouser_is_skipped_once_its_daily_quota_is_spent(tmp_path, monkeypatch, real_mouser):
    """Mouser's free tier is a DAILY budget. Once spent, every call returns
    403 "Maximum calls per day exceeded" — asking again costs a round trip on
    every lookup and can never succeed. After two strikes the source is skipped
    until the process restarts, which is also when a raised quota takes effect."""
    from heaviside.librarian.fetcher import lookup as lookup_mod

    monkeypatch.setattr(lookup_mod, "_mouser_quota_strikes", 0)
    calls = []

    class _OutOfQuota:
        def __init__(self, *a, **k):
            calls.append(1)

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def get_product(self, mpn):
            raise RuntimeError(
                'mouser 403: {"Code":"TooManyRequests","Message":'
                '"Maximum calls per day exceeded.","ResourceKey":"MaxCallPerDay"}')

    monkeypatch.setattr("heaviside.librarian.fetcher.mouser.MouserClient", _OutOfQuota)
    outcomes = []
    for _ in range(4):
        out = lookup_part(_MPN, "mosfet", client=_FakeDK(None), staging_root=tmp_path)
        outcomes.append([a for a in out["attempts"] if a["source"] == "mouser"][0]["outcome"])

    # the first two try and report the quota; the rest skip without a call
    assert len(calls) == 2, f"asked Mouser {len(calls)} times after it ran out"
    assert "daily call quota is spent" in outcomes[0]
    assert "skipped" in outcomes[-1]
    # and it is never silent: every attempt still says what happened
    assert all(o for o in outcomes)
