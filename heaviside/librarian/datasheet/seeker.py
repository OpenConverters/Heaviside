"""Datasheet-seeker cache — sourced specs for out-of-DB cross-reference originals.

The `datasheet-seeker` agent (Haiku + web) reads a part's real datasheet and
returns its electrical specs. Those land here, in a small on-disk cache keyed by
MPN, which the cross-reference param-check consults BEFORE the deterministic
datasheet fetch. This gives the tool the same advantage a senior FAE has — it
pulls the original's datasheet — without writing a full schema envelope into the
shared DB (which the nightly re-fetch would race) and without a live web call in
the headless pipeline.

Every cached value is grounded in a fetched datasheet by the seeker agent (no
fabrication); a field the datasheet lacked is simply absent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _cache_path() -> Path:
    p = os.environ.get("HEAVISIDE_SEEKER_CACHE")
    if p:
        return Path(p)
    return Path.home() / ".heaviside" / "seeker_cache.json"


def _load() -> dict[str, Any]:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _key(category: str, mpn: str) -> str:
    return f"{category}:{str(mpn).strip().lower()}"


def read(category: str, mpn: str) -> dict[str, Any] | None:
    """Return the seeker-sourced summary dict for (category, mpn), or None."""
    if not mpn:
        return None
    return _load().get(_key(category, mpn))


def write(category: str, mpn: str, summary: dict[str, Any]) -> None:
    """Persist a summary-keyed spec dict for (category, mpn)."""
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load()
    data[_key(category, mpn)] = summary
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


_SEEKER_SYSTEM = """You extract the REAL electrical specifications of one electronic component for a cross-reference tool. NO fabrication.

If DATASHEET TEXT is provided below, extract ONLY from it — every value must appear in that text; anything not present is null.
If NO datasheet text is provided, give the specs from your knowledge of this EXACT part number, but be conservative: return null for any field you are not confident is the datasheet value for this exact MPN. Never guess a "typical" value.

SI base units (H, A, Ω, F, V, s). Capture saturation current WITH its inductance-drop % and prefer the STANDARD rated current (IR,40K) over any best-case performance figure.

Return ONLY a JSON object. For a magnetic/inductor:
{"mpn":"","source":"datasheet_text|knowledge","inductance_H":null,"tolerance_frac":null,"isat_A":{"drop_10pct":null,"drop_20pct":null,"drop_30pct":null},"rated_current_A":null,"rated_current_basis":"","dcr_ohm":{"typ":null,"max":null},"dimensions_mm":{"length":null,"width":null,"height":null},"shielded":null}
For capacitor: capacitance_F, voltage_V, dielectric_code, temp_max_C, esr_ohm, ripple_current_A, tolerance_frac.
For mosfet: vds_V, rds_on_ohm, id_A, qg_C, vgs_th_max_V, temp_max_C. For diode: vrrm_V, vf_V, if_A, qrr_C, trr_s, temp_max_C.
"""


def _parse_json_lenient(raw: str) -> Any:
    """Parse a JSON object from an LLM reply that may wrap it in a ```json fence
    or add prose. Returns the parsed value or None."""
    if not raw:
        return None
    text = raw.strip()
    if "```" in text:
        # take the content of the first fenced block
        import re as _re

        m = _re.search(r"```(?:json)?\s*(.*?)```", text, _re.DOTALL)
        if m:
            text = m.group(1).strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        # last resort: the outermost {...}
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except (ValueError, TypeError):
                return None
    return None


def _magnetic_result_ok(specs: dict[str, Any]) -> bool:
    """A magnetic seek is 'good' if it pinned at least one current rating —
    without Isat or IR the gate cannot judge adequacy, so a reasoning retry is
    warranted."""
    if not isinstance(specs, dict):
        return False
    if isinstance(specs.get("rated_current_A"), (int, float)):
        return True
    isat = specs.get("isat_A") or {}
    return any(isinstance(isat.get(k), (int, float)) for k in ("drop_10pct", "drop_20pct", "drop_30pct"))


def kimi_seek(
    mpn: str,
    manufacturer: str,
    category: str,
    *,
    datasheet_text: str | None = None,
) -> dict[str, Any] | None:
    """Source a part's specs with a Kimi k2.5 call (Heaviside's available LLM) —
    the in-prod replacement for the Haiku seeker. Grounded in ``datasheet_text``
    when provided, else Kimi's knowledge of the exact MPN (conservative). Kimi
    is called NON-REASONING first (fast); if the magnetic result pins no current
    rating, it retries THAT call with reasoning on. Returns the parsed spec dict
    (with an ``mpn`` field) or None."""
    import json as _json
    import os as _os

    from heaviside.agents.llm_call import LLMCallError, call_llm

    user = f"Component: manufacturer={manufacturer}, MPN={mpn}, category={category}.\n"
    if datasheet_text:
        user += "\nDATASHEET TEXT:\n" + datasheet_text[:12000]
    else:
        user += "\n(no datasheet text available — use your knowledge of this exact MPN, conservatively)"

    def _call(reasoning: bool) -> dict[str, Any] | None:
        prev = _os.environ.get("HEAVISIDE_KIMI_DISABLE_THINKING")
        _os.environ["HEAVISIDE_KIMI_DISABLE_THINKING"] = "0" if reasoning else "1"
        try:
            raw = call_llm(_SEEKER_SYSTEM, user, model="kimi-k2.5", json_mode=True, max_tokens=1500)
        except LLMCallError:
            return None
        finally:
            if prev is None:
                _os.environ.pop("HEAVISIDE_KIMI_DISABLE_THINKING", None)
            else:
                _os.environ["HEAVISIDE_KIMI_DISABLE_THINKING"] = prev
        obj = _parse_json_lenient(raw)
        if not isinstance(obj, dict):
            return None
        obj.setdefault("mpn", mpn)
        return obj

    specs = _call(reasoning=False)  # non-reasoning first (fast)
    # Retry with reasoning only when the fast pass gave a weak magnetic result.
    if category == "magnetic" and not _magnetic_result_ok(specs or {}):
        retried = _call(reasoning=True)
        if retried is not None and _magnetic_result_ok(retried):
            specs = retried
    return specs


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _capacitor_summary_from_seeker(specs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"mpn": specs.get("mpn")}
    for src, dst in (
        ("capacitance_F", "capacitance"),
        ("voltage_V", "voltage"),
        ("esr_ohm", "esr"),
        ("ripple_current_A", "ripple_current"),
        ("temp_max_C", "temp_max_C"),
    ):
        v = _num(specs.get(src))
        if v is not None:
            out[dst] = v
    if isinstance(specs.get("capacitance_F"), (int, float)):
        out["value_si"] = float(specs["capacitance_F"])
    if specs.get("dielectric_code"):
        out["dielectric_code"] = str(specs["dielectric_code"])
    tf = _num(specs.get("tolerance_frac"))
    if tf is not None:
        out["tolerance_pct"] = tf * 100.0
    return out


def _mosfet_summary_from_seeker(specs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"mpn": specs.get("mpn")}
    for src, dst in (
        ("vds_V", "vds"),
        ("rds_on_ohm", "rds_on"),
        ("id_A", "rated_current"),
        ("qg_C", "qg"),
        ("vgs_th_max_V", "vgs_threshold_max"),
        ("temp_max_C", "temp_max_C"),
    ):
        v = _num(specs.get(src))
        if v is not None:
            out[dst] = v
    return out


def _diode_summary_from_seeker(specs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"mpn": specs.get("mpn")}
    for src, dst in (
        ("vrrm_V", "vrrm"),
        ("vf_V", "vf"),
        ("if_A", "if_avg"),
        ("qrr_C", "qrr"),
        ("trr_s", "trr"),
        ("temp_max_C", "temp_max_C"),
    ):
        v = _num(specs.get(src))
        if v is not None:
            out[dst] = v
    return out


def _resistor_summary_from_seeker(specs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"mpn": specs.get("mpn")}
    for src, dst in (
        ("resistance_ohm", "resistance"),
        ("power_W", "power_rating"),
        ("tcr_ppm", "tcr"),
        ("temp_max_C", "temp_max_C"),
    ):
        v = _num(specs.get(src))
        if v is not None:
            out[dst] = v
    if isinstance(specs.get("resistance_ohm"), (int, float)):
        out["value_si"] = float(specs["resistance_ohm"])
    tf = _num(specs.get("tolerance_frac"))
    if tf is not None:
        out["tolerance_pct"] = tf * 100.0
    return out


_SUMMARY_BY_CATEGORY = {
    "capacitor": _capacitor_summary_from_seeker,
    "mosfet": _mosfet_summary_from_seeker,
    "diode": _diode_summary_from_seeker,
    "resistor": _resistor_summary_from_seeker,
}


def summary_from_seeker(category: str, specs: dict[str, Any]) -> dict[str, Any]:
    """Map a raw datasheet-seeker JSON (from `kimi_seek`) to the flat
    `_summarize_candidate`-keyed dict the cross-reference gates read, per
    category. Magnetic/chipBead use the current-rating mapper; the rest map their
    category's electrical fields. Absent fields are omitted — never guessed."""
    if not isinstance(specs, dict):
        return {}
    if category in ("magnetic", "inductor", "chipBead"):
        return magnetic_summary_from_seeker(specs)
    fn = _SUMMARY_BY_CATEGORY.get(category)
    return fn(specs) if fn else {"mpn": specs.get("mpn")}


def magnetic_summary_from_seeker(specs: dict[str, Any]) -> dict[str, Any]:
    """Map a datasheet-seeker magnetic JSON to the _summarize_candidate keys the
    gates read. Isat uses the CONSERVATIVE lowest-drop value (10% preferred);
    rated current is the standard IR when the seeker marked it so. Absent fields
    are omitted — never guessed."""
    out: dict[str, Any] = {"mpn": specs.get("mpn")}
    L = specs.get("inductance_H")
    # Plausibility guard: a power inductor is µH–mH; a value ≥ 0.1 H means the
    # LLM dropped an SI prefix (e.g. wrote 4.7 for 4.7µH). Drop it rather than
    # cache a wrong value — the BOM's stated value is authoritative for the
    # primary-value gate anyway; the seeker's job is the current ratings.
    if isinstance(L, (int, float)) and 0 < L < 0.1:
        out["inductance"] = float(L)
        out["value_si"] = float(L)
    isat = specs.get("isat_A") or {}
    for k in ("drop_10pct", "drop_20pct", "drop_30pct"):
        v = isat.get(k)
        if isinstance(v, (int, float)):
            out["saturation_current"] = float(v)
            out["saturation_current_drop_pct"] = int(k.split("_")[1].rstrip("pct"))
            break
    rc = specs.get("rated_current_A")
    if isinstance(rc, (int, float)):
        out["rated_current"] = float(rc)
    dcr = specs.get("dcr_ohm") or {}
    # gate uses the max (worst-case) DCR when present, else typ.
    for k in ("max", "typ"):
        v = dcr.get(k)
        if isinstance(v, (int, float)):
            out["dcr"] = float(v)
            break
    dims = specs.get("dimensions_mm") or {}
    if any(isinstance(dims.get(k), (int, float)) for k in ("length", "width", "height")):
        out["dimensions_mm"] = {
            k: dims.get(k) for k in ("length", "width", "height") if isinstance(dims.get(k), (int, float))
        }
    return out
