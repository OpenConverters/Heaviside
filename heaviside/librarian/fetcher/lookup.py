"""One part number in, one staged catalogue record out — or a stated refusal.

Three sources are tried in order, and the order is the point: each one is
less curated than the last, so the first that can answer completely wins.

1. **Digi-Key** — a curated parametric table. Best when it has the part.
2. **Mouser** — the same kind of data from a second catalogue. Free capability
   that was already configured and unused; it holds parts Digi-Key does not.
3. **The datasheet itself** — found by web search or handed over by a
   distributor, fetched, and read by a language model. Last because it is the
   only source where the numbers are extracted rather than looked up.

Step 3 also rescues a case steps 1 and 2 fail on for a silly reason: a
distributor that HAS the part but does not publish one required field. Digi-Key
rarely lists Coss, so the MOSFET converter refuses real parts over a number the
datasheet prints on page 2. When that happens the distributor's own datasheet
URL is carried into step 3 rather than starting a fresh search.

This is the librarian's answer to "the catalogue does not have this part". A
caller (today: Faraday's part inspector, which finds a component on a PCB whose
MPN Kelvin cannot resolve) hands over the part number; the librarian looks it up
at the distributor, converts it to the category envelope, schema-validates it,
and PARKS IT IN STAGING for review. It is deliberately not appended to TAS here:
``stage_fetch`` -> human/auditor -> ``apply_staged`` -> ``add_component`` is the
existing path into the catalogue, and a web button must not be a way around it.

The one thing this module exists to get right is the difference between
**"we asked and the part is not there"** and **"we could not ask"**.
``fetch_dk_product`` collapses both into ``None`` (it logs and swallows
transport errors), and an endpoint built on that would tell a user their part
does not exist at Digi-Key whenever a token had expired. A caller acting on
that would go and invent the part by hand. So the distributor call is made
here, where a failure to reach the distributor propagates as itself.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from heaviside.librarian.fetcher.original import (
    _mpn_matches,
    classify_dk_product,
    fetch_original_envelope,
)
from heaviside.librarian.fetcher.staging import stage_fetch

logger = logging.getLogger(__name__)

__all__ = ["LookupResult", "lookup_part", "MAX_MPN_LENGTH"]

# A part number is a part number. Anything longer is a paste accident or an
# attempt to use the distributor as a search engine on someone else's budget.
MAX_MPN_LENGTH = 64


class LookupResult(dict):
    """The lookup's answer. A dict so the API layer can return it verbatim.

    Keys: ``mpn``, ``found``; then on a hit ``category``, ``component``,
    ``source``, ``stored`` (staging path or ``None``) and ``storedReason``;
    on a miss ``reason``.
    """


def _dk_exact(client: Any, mpn: str) -> dict[str, Any] | None:
    """Exact-MPN Digi-Key lookup where being unable to ASK raises.

    Mirrors ``fetch_dk_product``'s two-step (detail endpoint, then keyword
    search filtered to an exact MPN match) but lets the search call's failure
    out. The detail endpoint's failure stays swallowed: not every account has
    it, and the search path is the real lookup.
    """
    try:
        detail = client.get_product(mpn)
        if isinstance(detail, dict) and _mpn_matches(detail, mpn):
            return detail
    except Exception as exc:  # noqa: BLE001 — the detail endpoint is optional
        logger.debug("get_product(%s) unavailable, using search: %s", mpn, exc)

    res = client.search(mpn, limit=10)  # DistributorError propagates: see docstring
    products = res.get("Products", []) if isinstance(res, dict) else []
    for p in products:
        if _mpn_matches(p, mpn):
            return p
    return None


def lookup_part(
    mpn: str,
    category: str | None = None,
    *,
    client: Any = None,
    staging_root: Path | None = None,
    allow_datasheet: bool = True,
) -> LookupResult:
    """Source ``mpn`` from the distributors, then from its datasheet, and stage it.

    Args:
        mpn: the manufacturer part number, exactly as it should be matched.
        category: an optional HINT from the caller ("mosfet", "capacitor", …).
            A board only guesses this from a refdes and a footprint, so a
            distributor's own taxonomy overrules it wherever one is available.
            It IS used for the datasheet route, which has no taxonomy of its
            own — and the result says so.
        client: a Digi-Key client (test hook). Built from the environment's
            credentials when omitted, and a missing credential raises.
        staging_root: override the staging directory (test hook).
        allow_datasheet: run step 3. Off in tests that must not touch the web.

    Returns:
        A :class:`LookupResult`. ``attempts`` always lists what each source
        said, so a miss is explained rather than bare.

    Raises:
        ValueError: the MPN is empty or implausibly long.
        DistributorError / CredentialError: DIGI-KEY could not be reached — NOT
            the same as the part being absent, and never reported as such.
            These are siblings under ``FetcherError``, not parent and child, so
            a caller must name both. A failure of the LATER sources is demoted
            to an attempt note: by then Digi-Key has already answered, and one
            flaky secondary source must not sink a lookup that got that far.
    """
    mpn = (mpn or "").strip()
    if not mpn:
        raise ValueError("a part number is required")
    if len(mpn) > MAX_MPN_LENGTH:
        raise ValueError(
            f"part number is {len(mpn)} characters; the limit is {MAX_MPN_LENGTH}"
        )

    attempts: list[dict[str, Any]] = []
    # carried from a distributor that found the part but could not describe it
    fallback_url: str | None = None
    fallback_mfr: str = ""
    resolved_category: str = ""

    # ---- 1. Digi-Key --------------------------------------------------------
    owned = client is None
    if owned:
        from heaviside.librarian.fetcher.auth import load_credentials
        from heaviside.librarian.fetcher.digikey import DigiKeyClient

        # require_digikey=True so an unconfigured box raises
        # MissingCredentialError here — a CredentialError the endpoint maps to
        # "could not reach the distributor" — instead of handing the client a
        # None it would reject with a less honest message.
        creds = load_credentials(require_digikey=True)
        client = DigiKeyClient(creds.digikey)
    try:
        product = _dk_exact(client, mpn)
    finally:
        if owned:
            client.close()

    if product is None:
        attempts.append({"source": "digikey", "outcome": "no part with exactly this number"})
    else:
        # The hint only survives if the distributor's own taxonomy has no
        # opinion. A board's guess ("Q1 in a SOT-23, so a MOSFET") is weaker
        # evidence than the distributor's product family, and letting a wrong
        # hint pick the converter is how a BJT becomes a schema-valid MOSFET.
        resolved_category = classify_dk_product(product) or (category or "")
        envelope, info = fetch_original_envelope(client, mpn, resolved_category, product=product)
        if envelope is not None:
            attempts.append({"source": "digikey", "outcome": "found"})
            return _stage_and_return(mpn, envelope, info, "digikey", product,
                                     staging_root, attempts)
        attempts.append({"source": "digikey", "outcome": info})
        fallback_url = _dk_datasheet_url(product)
        fallback_mfr = str((product.get("Manufacturer") or {}).get("Value") or "")

    # ---- 2. Mouser ----------------------------------------------------------
    m_product, note = _mouser_exact(mpn)
    if m_product is None:
        attempts.append({"source": "mouser", "outcome": note})
    else:
        envelope, info = _mouser_envelope(m_product, mpn, category or resolved_category)
        if envelope is not None:
            attempts.append({"source": "mouser", "outcome": "found"})
            return _stage_and_return(mpn, envelope, info, "mouser", m_product,
                                     staging_root, attempts)
        attempts.append({"source": "mouser", "outcome": info})
        if not fallback_url:
            fallback_url = str(m_product.get("DataSheetUrl") or "") or None
            fallback_mfr = fallback_mfr or str(m_product.get("Manufacturer") or "")

    # ---- 3. the datasheet ---------------------------------------------------
    if not allow_datasheet:
        return LookupResult(mpn=mpn, found=False, attempts=attempts,
                            reason="no distributor carries this part number")

    # Singular category for the datasheet route: the distributor's word if we
    # got one, else the caller's hint. Stated in the result either way, because
    # a hint that turns out wrong is why a reading can come back empty.
    ds_category = _singular(resolved_category) or _singular(category or "")
    if not ds_category:
        attempts.append({"source": "datasheet",
                         "outcome": "no category is known for this part, and the "
                                    "datasheet route needs one to know what to read for"})
        return LookupResult(mpn=mpn, found=False, attempts=attempts,
                            reason="no distributor carries this part number, and nothing "
                                   "said what kind of part it is")
    if ds_category not in DATASHEET_CATEGORIES:
        attempts.append({"source": "datasheet",
                         "outcome": f"reading a datasheet is only supported for "
                                    f"{', '.join(DATASHEET_CATEGORIES)}, not {ds_category}"})
        return LookupResult(mpn=mpn, found=False, attempts=attempts,
                            reason="no distributor carries this part number")

    try:
        from heaviside.librarian.fetcher.from_datasheet import envelope_from_datasheet

        envelope, info, detail = envelope_from_datasheet(
            mpn, ds_category, manufacturer=fallback_mfr, datasheet_url=fallback_url)
    except Exception as exc:  # noqa: BLE001 — a last-resort source must not sink the answer
        logger.info("datasheet route failed for %s: %s", mpn, exc)
        attempts.append({"source": "datasheet", "outcome": f"could not be read: {exc}"})
        return LookupResult(mpn=mpn, found=False, attempts=attempts,
                            reason="no distributor carries this part number, and its "
                                   "datasheet could not be read")
    if envelope is None:
        attempts.append({"source": "datasheet", "outcome": info,
                         "read": detail.get("read"), "tried": detail.get("tried")})
        return LookupResult(mpn=mpn, found=False, attempts=attempts, reason=info)

    attempts.append({"source": "datasheet", "outcome": "found",
                     "read": detail.get("read")})
    return _stage_and_return(mpn, envelope, info, "datasheet", None,
                             staging_root, attempts, read=detail.get("read"))


# ---------------------------------------------------------------------------
# the sources
# ---------------------------------------------------------------------------

# Only what from_datasheet can map without guessing. Kept here so the chain can
# say "not supported for this kind of part" before spending a web search.
DATASHEET_CATEGORIES = ("mosfet", "diode", "capacitor", "resistor", "igbt",
                        "connector", "varistor")

_TO_SINGULAR = {
    "mosfets": "mosfet", "diodes": "diode", "capacitors": "capacitor",
    "resistors": "resistor", "igbts": "igbt", "magnetics": "magnetic",
}


def _singular(category: str) -> str:
    c = (category or "").strip()
    return _TO_SINGULAR.get(c, c)


def _dk_datasheet_url(product: dict[str, Any]) -> str | None:
    for key in ("PrimaryDatasheet", "DatasheetUrl", "PrimaryDatasheetUrl"):
        v = product.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v
    return None


# Mouser's free tier is a DAILY call budget, and when it is spent every request
# comes back 403 {"Code":"TooManyRequests","Message":"Maximum calls per day
# exceeded."}. Asking again costs a round trip on every lookup and can never
# succeed, so after a couple of those the source is skipped until the process
# restarts — which is also when a raised quota or a new key would take effect.
# It is a latency guard, never a silence: the trail still says Mouser was
# skipped and why.
_MOUSER_EXHAUSTED_AFTER = 2
_mouser_quota_strikes = 0


def _mouser_out_of_quota(exc: Exception) -> bool:
    text = str(exc).lower()
    return "maxcallperday" in text or "maximum calls per day" in text or (
        "toomanyrequests" in text and "day" in text)


def _mouser_exact(mpn: str) -> tuple[dict[str, Any] | None, str]:
    """Mouser's answer for an exact MPN, as (product, note).

    Mouser is a SECONDARY source here: Digi-Key has already spoken, so being
    unable to reach Mouser must not turn into an exception that discards that.
    The reason is returned as a note instead — and it names which of the two
    things happened, so "Mouser is rate limited" never reads as "Mouser does
    not have it".
    """
    global _mouser_quota_strikes
    if _mouser_quota_strikes >= _MOUSER_EXHAUSTED_AFTER:
        return None, ("skipped — Mouser's daily call quota is spent, and asking "
                      "again cannot succeed until it resets or the plan is raised")
    try:
        from heaviside.librarian.fetcher.auth import load_credentials
        from heaviside.librarian.fetcher.mouser import MouserClient

        creds = load_credentials()
        if not getattr(creds, "mouser", None):
            return None, "no Mouser credentials are configured"
        with MouserClient(creds.mouser) as m:
            product = m.get_product(mpn)
        _mouser_quota_strikes = 0        # a success clears the count
        if product is None:
            return None, "no part with exactly this number"
        return product, ""
    except Exception as exc:  # noqa: BLE001 — see docstring
        if _mouser_out_of_quota(exc):
            _mouser_quota_strikes += 1
            return None, ("its daily call quota is spent (Mouser: maximum calls "
                          "per day exceeded)")
        return None, f"could not be asked ({type(exc).__name__}: {str(exc)[:120]})"


def _mouser_envelope(product: dict[str, Any], mpn: str,
                     hint: str) -> tuple[dict[str, Any] | None, str]:
    """Convert a Mouser row, validate it, and return (envelope, db_category)."""
    from heaviside.librarian.fetcher import convert as C

    category = C.detect_category(product, "mouser") or _plural(hint)
    if not category:
        return None, "could not tell what kind of part this is from the Mouser listing"
    converters = {
        "mosfets": C.convert_mouser_to_tas_mosfet,
        "diodes": C.convert_mouser_to_tas_diode,
        "igbts": C.convert_mouser_to_tas_igbt,
        "capacitors": C.convert_mouser_to_tas_capacitor,
        "resistors": C.convert_mouser_to_tas_resistor,
    }
    conv = converters.get(category)
    if conv is None:
        return None, f"no Mouser converter for category {category!r}"
    try:
        envelope = conv(product)
    except Exception as exc:  # noqa: BLE001 — reported, never raised past here
        return None, f"conversion failed: {str(exc)[:160]}"
    if not isinstance(envelope, dict):
        return None, "conversion produced no envelope"
    try:
        from heaviside.librarian.tas import ValidationError, validate_component

        validate_component(category, envelope)
    except ValidationError as exc:
        return None, f"failed {category} schema validation: {str(exc)[:160]}"
    except Exception as exc:
        return None, f"could not be validated: {exc}"
    return envelope, category


_TO_PLURAL = {v: k for k, v in _TO_SINGULAR.items()}


def _plural(category: str) -> str:
    c = (category or "").strip()
    return _TO_PLURAL.get(c, c if c.endswith("s") else "")


def _stage_and_return(mpn: str, envelope: dict[str, Any], db_category: str,
                      source: str, raw: dict[str, Any] | None,
                      staging_root: Path | None, attempts: list[dict[str, Any]],
                      read: str | None = None) -> LookupResult:
    """Park a validated record for review and describe where it went."""
    from heaviside.librarian.tas import component_exists

    stored: str | None = None
    if component_exists(db_category, mpn):
        stored_reason = (
            f"already in TAS/{db_category}.ndjson — the deployed search index is "
            "older than the catalogue, so rebuilding the index is what surfaces it"
        )
    else:
        path = stage_fetch(db_category, mpn, envelope, source=source,
                           raw_response=raw, staging_root=staging_root)
        stored = str(path)
        stored_reason = (
            "staged for review — a librarian applies it into TAS, and the next "
            "index build puts it in the catalogue"
        )
    out = LookupResult(mpn=mpn, found=True, category=db_category,
                       component=envelope, source=source, stored=stored,
                       storedReason=stored_reason, attempts=attempts)
    if read:
        out["readFrom"] = read
    return out
