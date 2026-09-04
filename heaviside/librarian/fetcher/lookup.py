"""One part number in, one staged catalogue record out — or a stated refusal.

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
) -> LookupResult:
    """Look ``mpn`` up at the distributor, validate it, and stage it.

    Args:
        mpn: the manufacturer part number, exactly as it should be matched.
        category: an optional HINT from the caller ("mosfet", "capacitor", …).
            A board only guesses this from a refdes and a footprint, so it is
            never trusted: when the hint yields no converter the distributor's
            own taxonomy decides instead.
        client: a Digi-Key client (test hook). Built from the environment's
            credentials when omitted, and a missing credential raises.
        staging_root: override the staging directory (test hook).

    Returns:
        A :class:`LookupResult`.

    Raises:
        ValueError: the MPN is empty or implausibly long.
        DistributorError / CredentialError: the distributor could not be
            reached, refused the request, or was never configured — NOT the
            same as the part being absent, and never reported as such. These
            are siblings under ``FetcherError``, not parent and child, so a
            caller must name both.
    """
    mpn = (mpn or "").strip()
    if not mpn:
        raise ValueError("a part number is required")
    if len(mpn) > MAX_MPN_LENGTH:
        raise ValueError(
            f"part number is {len(mpn)} characters; the limit is {MAX_MPN_LENGTH}"
        )

    owned = client is None
    if owned:
        from heaviside.librarian.fetcher.digikey import DigiKeyClient

        client = DigiKeyClient()  # MissingCredentialError propagates (a CredentialError)
    try:
        product = _dk_exact(client, mpn)
    finally:
        if owned:
            client.close()

    if product is None:
        return LookupResult(
            mpn=mpn,
            found=False,
            reason="Digi-Key has no part with exactly this number",
        )

    # The hint only survives if the distributor's own taxonomy has no opinion.
    # A board's guess ("Q1 in a SOT-23, so a MOSFET") is weaker evidence than
    # the distributor's product family, and letting a wrong hint pick the
    # converter is how a BJT becomes a schema-valid MOSFET.
    resolved = classify_dk_product(product) or (category or "")
    envelope, info = fetch_original_envelope(client, mpn, resolved, product=product)
    if envelope is None:
        # `info` is the refusal: unclassifiable, no converter, conversion
        # failed, or — the important one — failed schema validation. A part
        # that will not validate is not staged and not returned as data.
        return LookupResult(mpn=mpn, found=False, reason=info)

    db_category = info
    stored: str | None = None
    stored_reason: str | None = None
    from heaviside.librarian.tas import component_exists

    if component_exists(db_category, mpn):
        stored_reason = (
            f"already in TAS/{db_category}.ndjson — the deployed search index is "
            "older than the catalogue, so rebuilding the index is what surfaces it"
        )
    else:
        path = stage_fetch(
            db_category,
            mpn,
            envelope,
            source="digikey",
            raw_response=product,
            staging_root=staging_root,
        )
        stored = str(path)
        stored_reason = (
            "staged for review — a librarian applies it into TAS, and the next "
            "index build puts it in the catalogue"
        )

    return LookupResult(
        mpn=mpn,
        found=True,
        category=db_category,
        component=envelope,
        source="digikey",
        stored=stored,
        storedReason=stored_reason,
    )
