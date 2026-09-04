"""Find a part's datasheet on the open web, when no distributor carries it.

The librarian's sources are ranked by how much they can be trusted, and this is
the last one: a distributor API returns parametric fields somebody curated,
whereas this returns a URL that has to be fetched and read. It exists because
the alternative is telling a user their part does not exist when the
manufacturer publishes its datasheet openly — which is what happened to
IPA045N10N3G, an Infineon part Digi-Key does not list.

There is no search API key anywhere in this project, so the search itself goes
through :func:`heaviside.pipeline.url_fetch.fetch_document`, which carries
browser headers and escalates to a headless browser when a CDN blocks it. That
is also the SSRF guard, so no URL discovered here can point at the private
network.

Ranking is the whole value of this module. A part number typed into a search
engine returns, in order: datasheet aggregators that wrap the real PDF in ads,
marketplace listings, and — usually further down — the manufacturer's own PDF.
The manufacturer's copy is the only one worth extracting specs from, so it is
ranked first and the aggregators last, rather than taking whatever came top.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass

from heaviside.librarian.fetcher.base import FetcherError

logger = logging.getLogger(__name__)

__all__ = ["DatasheetCandidate", "SearchUnavailable", "find_datasheet_urls"]


class SearchUnavailable(FetcherError):
    """The web search could not be performed (blocked, offline, no results page).

    Distinct from "the search ran and found nothing": a caller that conflates
    them would report a part as non-existent because a search engine was down.
    """


@dataclass(frozen=True)
class DatasheetCandidate:
    url: str
    host: str
    is_pdf: bool
    mentions_mpn: bool
    rank: float
    why: str


# Sites that republish other people's datasheets wrapped in adware. They do
# often hold the part, so they are kept as a last resort rather than dropped —
# but never preferred over the manufacturer, whose copy is authoritative and
# current.
_AGGREGATORS = (
    "alldatasheet", "datasheetspdf", "datasheetcatalog", "datasheets360",
    "chipfind", "datasheet4u", "icpdf", "elcodis", "datasheetq",
    "datasheetarchive", "oemstrade", "findchips", "octopart", "lcsc",
    "utsource", "ariat-tech", "componentsearchengine", "datasheet.iiic",
    "iiic.cc", "datasheets.com", "datasheetspdf", "pdf1.", "html.alldatasheet",
    "kynix", "chipdocs", "datasheetbank", "hqew", "seekic", "alldatasheetde",
)
# Semiconductor and passive makers, by the domain they publish documents on.
# Used twice: to rank a candidate (a PDF served by a maker beats a PDF served by
# a mirror, even when nobody told us which maker to expect) and to name the
# manufacturer of a part no distributor carries.
#
# Without this, a search for a bare MPN has no manufacturer token to match, so
# every unrecognised host looked alike and eupecsemi.com — a mirror with a
# self-signed certificate — outranked infineon.com for an Infineon part.
MANUFACTURER_DOMAINS = {
    "ti.com": "Texas Instruments", "st.com": "STMicroelectronics",
    "nxp.com": "NXP Semiconductors", "onsemi.com": "onsemi",
    "diodes.com": "Diodes Incorporated", "vishay.com": "Vishay",
    "rohm.com": "ROHM", "toshiba.semicon-storage.com": "Toshiba",
    "infineon.com": "Infineon Technologies", "microchip.com": "Microchip",
    "analog.com": "Analog Devices", "renesas.com": "Renesas",
    "epc-co.com": "EPC", "wolfspeed.com": "Wolfspeed",
    "we-online.com": "Wurth Elektronik", "murata.com": "Murata",
    "nexperia.com": "Nexperia", "littelfuse.com": "Littelfuse",
    "mccsemi.com": "Micro Commercial Components",
    "alphaandomega.com": "Alpha & Omega", "aosmd.com": "Alpha & Omega Semiconductor",
    "semtech.com": "Semtech", "ixys.com": "IXYS", "qorvo.com": "Qorvo",
    "mouser-semi.com": "", "fairchildsemi.com": "Fairchild",
    "irf.com": "International Rectifier", "vishay.de": "Vishay",
    "kemet.com": "KEMET", "avx.com": "AVX", "yageo.com": "YAGEO",
    "tdk.com": "TDK", "taiyo-yuden.com": "Taiyo Yuden",
    "samsungsem.com": "Samsung Electro-Mechanics", "panasonic.com": "Panasonic",
    "nichicon.co.jp": "Nichicon", "rubycon.co.jp": "Rubycon",
    "coilcraft.com": "Coilcraft", "bourns.com": "Bourns",
    "abracon.com": "Abracon", "epcos.com": "EPCOS",
    "skyworksinc.com": "Skyworks", "power.com": "Power Integrations",
    "monolithicpower.com": "Monolithic Power Systems",
    "silabs.com": "Silicon Labs", "maximintegrated.com": "Maxim Integrated",
    "cree.com": "Cree", "transphormusa.com": "Transphorm",
    "navitassemi.com": "Navitas", "gansystems.com": "GaN Systems",
}


def manufacturer_domain(host: str) -> str | None:
    """The manufacturer domain ``host`` belongs to, if any."""
    host = (host or "").lower()
    bare = host[4:] if host.startswith("www.") else host
    for domain in MANUFACTURER_DOMAINS:
        if bare == domain or bare.endswith("." + domain):
            return domain
    return None


# Distributors: useful, authoritative-ish, but a listing page is not a datasheet.
_DISTRIBUTORS = (
    "digikey.", "mouser.", "farnell.", "rs-online.", "newark.", "arrow.com",
    "avnet.", "tme.eu", "conrad.", "reichelt.",
)

_SEARCH_URL = "https://html.duckduckgo.com/html/?q={q}"
# DuckDuckGo's HTML results wrap every outbound link as /l/?uddg=<encoded>
_LINK_RE = re.compile(r"uddg=([^&\"']+)")


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _mfr_tokens(manufacturer: str | None) -> list[str]:
    """Host-matchable pieces of a manufacturer name.

    "Infineon Technologies" -> ["infineon"]; corporate suffixes carry no
    signal and would match half the web.
    """
    if not manufacturer:
        return []
    drop = {"technologies", "technology", "semiconductor", "semiconductors",
            "electronics", "electronic", "corporation", "corp", "inc", "ltd",
            "limited", "gmbh", "co", "company", "group", "industries", "llc",
            "sa", "ag", "kk", "plc", "the", "and"}
    toks = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in manufacturer.split()]
    return [t for t in toks if len(t) >= 4 and t not in drop]


def _score(url: str, mpn: str, mfr_tokens: list[str]) -> DatasheetCandidate | None:
    host = _host(url)
    if not host or not url.lower().startswith(("http://", "https://")):
        return None
    low = url.lower()
    squashed = re.sub(r"[^a-z0-9]", "", mpn.lower())
    url_squashed = re.sub(r"[^a-z0-9]", "", low)
    mentions = squashed in url_squashed
    is_pdf = low.endswith(".pdf") or ".pdf?" in low

    rank = 0.0
    why = []
    if is_pdf:
        rank += 3.0
        why.append("a PDF")
    if mentions:
        rank += 2.0
        why.append("names the part")
    if any(a in host for a in _AGGREGATORS):
        rank -= 4.0
        why.append("a datasheet aggregator")
    elif any(d in host for d in _DISTRIBUTORS):
        rank -= 1.0
        why.append("a distributor listing")
    elif mfr_tokens and any(t in host for t in mfr_tokens):
        # The strongest evidence: the host carries the expected maker's name.
        rank += 3.0
        why.append("the manufacturer's own site")
    elif manufacturer_domain(host):
        # A maker's domain, just not the one we were told to expect (usually
        # because nobody told us). Still far better than a mirror: an earlier
        # version credited every unrecognised host equally, which put
        # eupecsemi.com above infineon.com for an Infineon part.
        rank += 2.5
        why.append("a component manufacturer's site")
    else:
        why.append("an unrecognised site")
    if "datasheet" in low:
        rank += 0.5
    if low.startswith("http://"):
        rank -= 0.5          # a manufacturer serves its own documents over TLS
    return DatasheetCandidate(url=url, host=host, is_pdf=is_pdf,
                              mentions_mpn=mentions, rank=rank,
                              why=", ".join(why))


def find_datasheet_urls(
    mpn: str,
    manufacturer: str | None = None,
    *,
    limit: int = 6,
    timeout: float = 45.0,
    fetch=None,
) -> list[DatasheetCandidate]:
    """Search the open web for ``mpn``'s datasheet, best candidate first.

    Args:
        mpn: the exact part number.
        manufacturer: added to the query when known; it sharpens the results
            but is never required.
        limit: how many candidates to return.
        fetch: injection point for :func:`fetch_document` (test hook).

    Returns:
        Ranked :class:`DatasheetCandidate` objects, possibly empty when the
        search ran and genuinely matched nothing.

    Raises:
        SearchUnavailable: the search could not be run or its page could not be
            parsed — NOT the same as no results, and never reported as such.
    """
    mpn = (mpn or "").strip()
    if not mpn:
        raise ValueError("a part number is required")
    if fetch is None:
        from heaviside.pipeline.url_fetch import fetch_document as fetch

    terms = f"{manufacturer} {mpn} datasheet" if manufacturer else f"{mpn} datasheet"
    url = _SEARCH_URL.format(q=urllib.parse.quote_plus(terms))
    try:
        doc = fetch(url, timeout=timeout)
    except Exception as exc:  # DocumentFetchError and anything the layer raises
        raise SearchUnavailable(f"web search could not be reached: {exc}") from exc

    body = doc.content.decode("utf-8", "replace")
    raw = [urllib.parse.unquote(m) for m in _LINK_RE.findall(body)]
    if not raw and "result" not in body.lower():
        # No links AND nothing that looks like a results page: we were served a
        # challenge or an error, not an empty result set.
        raise SearchUnavailable(
            "the web search returned no results page (a bot challenge, most likely)")

    tokens = _mfr_tokens(manufacturer)
    seen: set[str] = set()
    out: list[DatasheetCandidate] = []
    for u in raw:
        u = u.split("#")[0]
        if u in seen:
            continue
        seen.add(u)
        cand = _score(u, mpn, tokens)
        if cand is not None:
            out.append(cand)
    out.sort(key=lambda c: c.rank, reverse=True)
    logger.debug("datasheet search for %s: %d candidate(s)", mpn, len(out))
    return out[:limit]
