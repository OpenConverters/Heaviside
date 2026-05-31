"""CRE (Competitor Reverse-Engineering) pipeline orchestrator.

Takes a reference design (PDF or description) and:
  1. Extracts specs + BOM (LLM: competitor + reverse-engineer agents)
  2. Verifies MPNs against TAS (deterministic)
  3. Designs a competing converter via the full_design pipeline
  4. Reviews the design (LLM: reviewer agent)
  5. Optionally: stress-tests (crowbar) and cost-challenges (hatchet)

The pipeline reuses Heaviside's existing infrastructure:
  - ``full_design()`` for the actual converter design
  - ``catalogue/selector`` for TAS lookups
  - ``sim/runner`` for ngspice simulation
  - ``pipeline/realism`` for the 10-check realism gate
  - ``pipeline/teacher`` for lesson extraction

Launch:
  heaviside reverse-engineer "TI TIDA-050072" --pdf path/to/pdf
  POST /cre {"reference": "...", "pdf_text": "..."}
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from heaviside.agents.llm_call import (
    LLMCallError,
    call_agent,
    call_agent_json,
    extract_json_block,
)
from heaviside.pipeline.cre import (
    CREOutcome,
    CREState,
    ReferenceClaims,
    ReferenceSpec,
)

logger = logging.getLogger(__name__)


def _float_or_none(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return f if f != 0 else None
    except (TypeError, ValueError):
        return None


def _first_output_field(specs: dict[str, Any], field: str, fallback: Any = 0) -> float:
    outputs = specs.get("outputs")
    if isinstance(outputs, list) and outputs and isinstance(outputs[0], dict):
        return float(outputs[0].get(field, fallback) or fallback)
    return float(fallback)


class CREPipelineError(RuntimeError):
    """Raised on unrecoverable CRE pipeline failures."""


# ---------------------------------------------------------------------------
# Stage 0: PDF extraction (deterministic)
# ---------------------------------------------------------------------------


def _stage0_extract_pdf(state: CREState) -> CREState:
    """Extract text from PDF if a path was provided."""
    if state.pdf_path is None:
        return state
    try:
        from heaviside.pipeline.pdf_extract import extract_pdf_text
        state.pdf_text = extract_pdf_text(state.pdf_path)
        logger.info("CRE stage 0: extracted %d chars from %s",
                     len(state.pdf_text), state.pdf_path)
    except Exception as exc:
        state.diagnostics.append(f"PDF extraction failed: {exc}")
        logger.warning("CRE stage 0: PDF extraction failed: %s", exc)
    return state


# ---------------------------------------------------------------------------
# Stage 1: Competitor analysis (LLM)
# ---------------------------------------------------------------------------


def _stage1_competitor(state: CREState) -> CREState:
    """Extract structured specs from the reference design."""
    user_msg = f"Reference design: {state.reference}\n\n"
    if state.pdf_text:
        user_msg += f"REFERENCE DESIGN PDF CONTENT:\n\n{state.pdf_text[:100_000]}"
    else:
        user_msg += "(No PDF provided — extract what you can from the name/description.)"

    try:
        data = call_agent_json("competitor", user_msg, max_tokens=8192, max_retries=2)
    except LLMCallError as exc:
        state.diagnostics.append(f"competitor agent failed after retries: {exc}")
        return state

    specs = data.get("specs", {})
    def _f(val: Any, default: float = 0.0) -> float:
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    try:
        vin_min = _f(specs.get("vin_min"), 85.0 if specs.get("input_type") == "ac" else 0.0)
        vin_nom = _f(specs.get("vin_nom"))
        vin_max = _f(specs.get("vin_max"))
        # Compute vin_nom from min/max if not extracted
        if vin_nom <= 0 and vin_max > 0 and vin_min > 0:
            vin_nom = (vin_min + vin_max) / 2
        elif vin_nom <= 0 and vin_max > 0:
            vin_nom = vin_max * 0.75
        ref = ReferenceSpec(
            topology=specs.get("topology", "unknown"),
            vin_min=vin_min,
            vin_nom=vin_nom,
            vin_max=vin_max,
            vout=_f(_first_output_field(specs, "voltage", specs.get("vout"))),
            iout=_f(_first_output_field(specs, "current", specs.get("iout"))),
            pout=_f(_first_output_field(specs, "power", specs.get("pout"))),
            fsw=_f(specs.get("switching_frequency")),
            efficiency_target=_float_or_none(data.get("performance", {}).get("efficiency")),
            isolation_required=bool(specs.get("isolation_required", False)),
            turns_ratio=_float_or_none(specs.get("turns_ratio")),
            rdson_hs=_float_or_none(specs.get("rdson_hs_mohm")),
            rdson_ls=_float_or_none(specs.get("rdson_ls_mohm")),
        )
        # Validate critical fields — zeros mean the LLM didn't extract them
        missing = []
        if ref.vin_max <= 0:
            missing.append("vin_max")
        if ref.vout <= 0:
            missing.append("vout")
        if ref.pout <= 0 and ref.iout <= 0:
            missing.append("pout or iout")
        if missing:
            state.diagnostics.append(
                f"competitor extracted incomplete spec — missing: {', '.join(missing)}. "
                f"Raw specs: {json.dumps(specs)[:300]}"
            )
        state.ref_spec = ref
    except (KeyError, TypeError, ValueError) as exc:
        state.diagnostics.append(f"spec extraction failed: {exc}")

    logger.info("CRE stage 1: extracted spec for %s (%s)",
                 state.reference, state.ref_spec.topology if state.ref_spec else "?")
    return state


# ---------------------------------------------------------------------------
# Stage 2: Reverse-engineer BOM (LLM)
# ---------------------------------------------------------------------------


def _stage2_reverse_engineer(state: CREState) -> CREState:
    """Extract the BOM from the reference design."""
    user_msg = f"Reference design: {state.reference}\n"
    if state.ref_spec:
        user_msg += f"Topology: {state.ref_spec.topology}\n"
        user_msg += f"Specs: {state.ref_spec.vout}V / {state.ref_spec.iout}A / {state.ref_spec.pout}W\n\n"
    if state.pdf_text:
        user_msg += f"REFERENCE DESIGN PDF CONTENT:\n\n{state.pdf_text[:100_000]}"

    # Scale tokens with PDF size — large BOMs need more output space
    bom_tokens = min(16384 + len(state.pdf_text or "") // 4, 32768)
    try:
        data = call_agent_json("reverse-engineer", user_msg, max_tokens=bom_tokens, max_retries=2)
    except LLMCallError as exc:
        state.diagnostics.append(f"reverse-engineer agent failed after retries: {exc}")
        return state

    state.ref_bom = data.get("bom", [])

    # Expand grouped ref_des into individual rows for downstream stages
    expanded: list[dict[str, Any]] = []
    for comp in state.ref_bom:
        ref = comp.get("ref_des", "")
        if "," in ref:
            refs = [r.strip() for r in ref.split(",") if r.strip()]
            for r in refs:
                row = dict(comp)
                row["ref_des"] = r
                row["quantity"] = 1
                expanded.append(row)
        else:
            expanded.append(comp)
    if len(expanded) > len(state.ref_bom):
        logger.info("CRE stage 2: expanded %d groups → %d individual components",
                     len(state.ref_bom), len(expanded))
        state.ref_bom = expanded
    if not state.ref_spec and "specs" in data:
        specs = data["specs"]
        try:
            state.ref_spec = ReferenceSpec(
                topology=data.get("topology", specs.get("topology", "unknown")),
                vin_min=float(specs.get("vin_min", 0)),
                vin_nom=float(specs.get("vin_nom", 0) or 0),
                vin_max=float(specs.get("vin_max", 0)),
                vout=float(specs.get("vout", 0)),
                iout=float(specs.get("iout", 0)),
                pout=float(specs.get("pout", 0)),
                fsw=float(specs.get("fsw", 0)),
                efficiency_target=_float_or_none(specs.get("efficiency_target")),
                isolation_required=bool(specs.get("isolation_required", False)),
                turns_ratio=_float_or_none(specs.get("turns_ratio")),
            )
        except (KeyError, TypeError, ValueError):
            pass

    logger.info("CRE stage 2: extracted %d BOM components", len(state.ref_bom))
    return state


# ---------------------------------------------------------------------------
# Stage 2.5: MPN verification (deterministic)
# ---------------------------------------------------------------------------


def _stage2_5_verify_mpns(state: CREState) -> CREState:
    """Check which BOM MPNs exist in the TAS database."""
    try:
        from heaviside.librarian.tas import component_exists
    except ImportError:
        state.diagnostics.append("TAS reader not available for MPN verification")
        return state

    _CAT_MAP = {
        "mosfet": "mosfets", "diode": "diodes", "capacitor": "capacitors",
        "magnetic": "magnetics", "inductor": "magnetics", "resistor": "resistors",
    }
    found = 0
    for comp in state.ref_bom:
        mpn = comp.get("mpn", comp.get("part", ""))
        if not mpn:
            continue
        cat = comp.get("category", comp.get("component_type", ""))
        ndjson_cat = _CAT_MAP.get(cat)
        if not ndjson_cat:
            continue
        try:
            comp["in_tas"] = component_exists(ndjson_cat, mpn)
            if comp["in_tas"]:
                found += 1
        except Exception:
            comp["in_tas"] = False

    logger.info("CRE stage 2.5: %d / %d MPNs found in TAS", found, len(state.ref_bom))

    # Fetch missing inductors/magnetics from Digi-Key — their DCR is
    # required for accurate simulation (no heuristic estimates allowed).
    _fetch_missing_magnetics(state)

    return state


def _fetch_missing_magnetics(state: CREState) -> None:
    """Fetch missing inductor MPNs from Digi-Key and persist to TAS."""
    try:
        from heaviside.librarian.fetcher.auth import load_credentials
        from heaviside.librarian.fetcher.convert import convert_digikey_to_tas_magnetic
        from heaviside.librarian.fetcher.digikey import DigiKeyClient
        from heaviside.librarian.tas import add_component, component_exists
    except ImportError as exc:
        logger.debug("librarian not available for magnetic fetch: %s", exc)
        return

    missing_magnetics = [
        comp for comp in state.ref_bom
        if comp.get("role", "") in ("mainInductor", "boostInductor", "buckInductor")
        and comp.get("mpn")
        and not comp.get("in_tas", False)
    ]
    if not missing_magnetics:
        return

    try:
        creds = load_credentials(require_digikey=True)
        client = DigiKeyClient(creds.digikey)
    except Exception as exc:
        logger.warning("CRE stage 2.6: cannot init Digi-Key client: %s", exc)
        for comp in missing_magnetics:
            state.diagnostics.append(
                f"inductor {comp['mpn']} not in TAS and Digi-Key unavailable: {exc}"
            )
        return

    with client:
        for comp in missing_magnetics:
            mpn = comp["mpn"]
            try:
                product = client.get_product(mpn)
                tas_record = convert_digikey_to_tas_magnetic(product)
                add_component("magnetics", tas_record)
                comp["in_tas"] = True
                logger.info("CRE stage 2.6: fetched and persisted inductor %s to TAS", mpn)
            except Exception as exc:
                state.diagnostics.append(
                    f"failed to fetch inductor {mpn} from Digi-Key: {exc}"
                )
                logger.warning("CRE stage 2.6: inductor %s fetch failed: %s", mpn, exc)


# ---------------------------------------------------------------------------
# Stage 3: Design competing converter (reuse full_design pipeline)
# ---------------------------------------------------------------------------


def _stage3_design(state: CREState) -> CREState:
    """Run the full_design pipeline for the extracted spec."""
    if state.ref_spec is None:
        state.diagnostics.append("no spec extracted — cannot design")
        return state
    if state.ref_spec.pout <= 0 or state.ref_spec.vout <= 0:
        state.diagnostics.append(
            f"spec incomplete (Pout={state.ref_spec.pout}, Vout={state.ref_spec.vout})"
        )
        return state

    from heaviside.pipeline.full_design import FullDesignError, full_design
    from heaviside.pipeline.topology_screen import feasible_topology_names

    spec_dict = state.ref_spec.to_heaviside_spec()

    # If the reference topology is known, prefer it
    topo = state.ref_spec.topology.lower().replace(" ", "_").replace("-", "_")

    def selector_fn(s: Mapping[str, Any]) -> tuple[list[str], str]:
        static = feasible_topology_names(s)
        if topo in static:
            return [topo] + [t for t in static if t != topo], f"CRE: prefer {topo}"
        return static, "CRE: static screen"

    try:
        stage1, stage2, outcomes = full_design(
            spec_dict,
            n_candidates_per_topology=3,
            parallel=False,
            selector_fn=selector_fn,
        )
        best = next(
            (o for o in outcomes
             if o.verdict_dict and o.verdict_dict["verdict"] == "pass"),
            outcomes[0] if outcomes else None,
        )
        state.design_outcome = best
        if best and best.verdict_dict:
            state.passed = best.verdict_dict["verdict"] == "pass"
        logger.info("CRE stage 3: design %s",
                     "PASSED" if state.passed else "FAILED/INCOMPLETE")
    except FullDesignError as exc:
        state.diagnostics.append(f"design pipeline failed: {exc}")

    return state


# ---------------------------------------------------------------------------
# Stage 4: Review (LLM)
# ---------------------------------------------------------------------------


def _stage4_review(
    state: CREState,
    *,
    max_attempts: int = 2,
) -> CREState:
    """Run the adversarial reviewer on the design outcome."""
    if state.design_outcome is None:
        state.diagnostics.append("no design to review")
        return state

    review_input = {
        "reference": state.reference,
        "ref_spec": state.ref_spec.__dict__ if state.ref_spec else {},
        "verdict": state.design_outcome.verdict_dict,
        "diagnostics": list(state.design_outcome.diagnostics),
    }
    if state.design_outcome.report:
        review_input["report"] = state.design_outcome.report

    for attempt in range(max_attempts):
        try:
            raw = call_agent(
                "reviewer",
                f"CRE REVIEW (adversarial mode)\n\n{json.dumps(review_input, indent=2)}",
                max_tokens=8192,
            )
            verdict_data = extract_json_block(raw)
            state.review_verdicts.append(verdict_data)
            verdict = verdict_data.get("verdict", "").upper()
            if verdict in ("APPROVED", "PROCEED"):
                state.passed = state.passed and True
                logger.info("CRE stage 4: review %s (attempt %d)",
                             verdict, attempt + 1)
                break
            logger.info("CRE stage 4: review %s (attempt %d), retrying",
                         verdict, attempt + 1)
        except LLMCallError as exc:
            state.diagnostics.append(f"review attempt {attempt + 1} failed: {exc}")
            break

    return state


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------


def _stage2_65_extract_rdson(state: CREState) -> CREState:
    """If Rds_on wasn't extracted from the eval board PDF, fetch the IC
    datasheet and extract it from there."""
    if not state.ref_spec or (state.ref_spec.rdson_hs and state.ref_spec.rdson_ls):
        return state

    # Find the controller IC MPN from the BOM
    ic_mpn = None
    for comp in state.ref_bom:
        role = comp.get("role", "")
        if role == "controller":
            ic_mpn = comp.get("mpn", comp.get("part", ""))
            if ic_mpn:
                break
    if not ic_mpn:
        return state

    # Try to get the IC datasheet URL from Digi-Key
    datasheet_url = None
    try:
        from heaviside.librarian.fetcher.auth import load_credentials
        from heaviside.librarian.fetcher.digikey import DigiKeyClient
        creds = load_credentials(require_digikey=True)
        with DigiKeyClient(creds.digikey) as client:
            # Try exact MPN first, then search with base part number
            try:
                product = client.get_product(ic_mpn)
                datasheet_url = product.get("PrimaryDatasheet", "")
            except Exception:
                # Strip package suffix and search (e.g. MP1653FGTF → MP1653F)
                import re as _re
                base_mpn = _re.sub(r"[A-Z]{2,3}(-[A-Z0-9]+)?$", "", ic_mpn)
                if base_mpn and base_mpn != ic_mpn:
                    results = client.search(base_mpn, limit=3)
                    for p in results.get("Products", []):
                        ds = p.get("PrimaryDatasheet", "")
                        if ds:
                            datasheet_url = ds
                            logger.info("CRE stage 2.65: found datasheet via search for %s", base_mpn)
                            break
    except Exception as exc:
        logger.debug("CRE stage 2.65: Digi-Key lookup for %s failed: %s", ic_mpn, exc)

    # Download and extract text from the IC datasheet
    ic_datasheet_text = ""
    urls_to_try = [datasheet_url] if datasheet_url else []

    # Also scan the eval board PDF text for IC datasheet URLs
    if state.pdf_text:
        import re as _re2
        pdf_urls_in_text = _re2.findall(
            r"https?://[^\s\"<>]+\.pdf",
            state.pdf_text,
        )
        for pu in pdf_urls_in_text:
            if pu not in urls_to_try:
                urls_to_try.append(pu)

    # Try known datasheet CDN patterns (Mouser, manufacturer sites)
    base_mpn = ic_mpn.rstrip("0123456789").rstrip("-")
    import re as _re2
    base_mpn = _re2.sub(r"[A-Z]{2,3}(-[A-Z0-9]+)?$", "", ic_mpn)
    if base_mpn:
        for pattern in [
            f"https://www.mouser.com/datasheet/2/277/{base_mpn}-*.pdf",
        ]:
            pass  # Can't glob URLs; rely on Digi-Key + PDF text scan

    for url in urls_to_try:
        if not url:
            continue
        try:
            import httpx
            resp = httpx.get(
                url, timeout=30.0, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and (
                "pdf" in ct or len(resp.content) > 50_000
            ):
                import tempfile
                from heaviside.pipeline.pdf_extract import extract_pdf_text
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                    f.write(resp.content)
                    tmp_path = f.name
                from pathlib import Path
                import os
                try:
                    ic_datasheet_text = extract_pdf_text(Path(tmp_path))
                finally:
                    os.unlink(tmp_path)
                if ic_datasheet_text:
                    logger.info(
                        "CRE stage 2.65: downloaded IC datasheet for %s "
                        "(%d chars) from %s",
                        ic_mpn, len(ic_datasheet_text), url[:60],
                    )
                    break
        except Exception as exc:
            logger.debug("CRE stage 2.65: download from %s failed: %s", url[:60], exc)

    if not ic_datasheet_text:
        state.diagnostics.append(
            f"Rds_on not in eval board PDF and IC datasheet for {ic_mpn} "
            f"not available — switch loss will use estimate"
        )
        return state

    # Ask LLM to extract Rds_on from the IC datasheet
    try:
        data = call_agent_json(
            "competitor",
            f"Extract ONLY the internal MOSFET Rds(on) specifications from this "
            f"IC datasheet for {ic_mpn}. Look in the Electrical Characteristics "
            f"table for RDS(ON) high-side and low-side values.\n\n"
            f"Reply with JSON: {{\"specs\": {{\"rdson_hs_mohm\": <value>, "
            f"\"rdson_ls_mohm\": <value>}}}}\n\n"
            f"IC DATASHEET TEXT:\n\n{ic_datasheet_text[:80_000]}",
            max_tokens=2048,
            max_retries=1,
        )
    except LLMCallError as exc:
        state.diagnostics.append(f"Rds_on extraction from IC datasheet failed: {exc}")
        return state

    specs = data.get("specs", {})
    rdson_hs = _float_or_none(specs.get("rdson_hs_mohm"))
    rdson_ls = _float_or_none(specs.get("rdson_ls_mohm"))

    if rdson_hs:
        old = state.ref_spec
        state.ref_spec = ReferenceSpec(
            topology=old.topology, vin_min=old.vin_min, vin_nom=old.vin_nom,
            vin_max=old.vin_max, vout=old.vout, iout=old.iout, pout=old.pout,
            fsw=old.fsw, efficiency_target=old.efficiency_target,
            isolation_required=old.isolation_required,
            turns_ratio=old.turns_ratio,
            rdson_hs=rdson_hs, rdson_ls=rdson_ls or rdson_hs,
            extra=old.extra,
        )
        logger.info("CRE stage 2.65: extracted Rds_on from %s datasheet: "
                     "HS=%.1fmΩ LS=%.1fmΩ",
                     ic_mpn, rdson_hs, rdson_ls or rdson_hs)

    return state


def _stage2_7_extract_claims(state: CREState) -> CREState:
    """Extract performance claims from the competitor agent's output."""
    if not state.ref_spec:
        return state
    # Claims are already partially in ref_spec (efficiency_target)
    # and in the competitor agent's 'performance' block.
    # Re-call the competitor with a focused claims-extraction prompt.
    if not state.pdf_text:
        return state

    # Also request Rds_on if still missing
    rdson_prompt = ""
    if state.ref_spec and not state.ref_spec.rdson_hs:
        rdson_prompt = (
            "\n\nALSO: extract the internal MOSFET Rds(on) for the main "
            "switching IC. Look for 'RDS(ON)', 'on-resistance', 'low-Rds', "
            "'internal MOSFET', or similar in the features list or electrical "
            "characteristics. Report as rdson_hs_mohm and rdson_ls_mohm in "
            "the specs block. If the PDF mentions values like '63mΩ and 36mΩ' "
            "or '130mΩ/65mΩ', extract those."
        )

    try:
        data = call_agent_json(
            "competitor",
            f"Extract ONLY the performance claims from this reference design PDF. "
            f"Focus on: efficiency at various load points, output ripple, thermal rise, "
            f"regulation specs, waveform descriptions.{rdson_prompt}\n\n"
            f"REFERENCE DESIGN PDF CONTENT:\n\n{state.pdf_text[:50_000]}",
            max_tokens=4096,
            max_retries=1,
        )
    except LLMCallError as exc:
        state.diagnostics.append(f"claims extraction failed: {exc}")
        return state

    perf = data.get("performance", {})

    # Build efficiency dict
    eff_dict: dict[str, float] = {}
    for entry in perf.get("efficiency_curve", []):
        load = entry.get("load_pct", 0)
        eff = entry.get("efficiency", 0)
        if load and eff:
            eff_dict[f"{load}%"] = eff
    if not eff_dict and perf.get("efficiency"):
        eff_dict["full_load"] = float(perf["efficiency"])

    state.ref_claims = ReferenceClaims(
        efficiency=eff_dict,
        vout_ripple_mv=perf.get("output_ripple_mv"),
        vin_ripple_mv=perf.get("input_ripple_mv"),
        vout_measured=perf.get("vout_measured"),
        thermal_rise_c=perf.get("thermal_rise_c"),
        load_regulation_pct=perf.get("load_regulation_pct"),
        line_regulation_pct=perf.get("line_regulation_pct"),
        waveform_descriptions=perf.get("waveforms", []),
    )
    # Extract Rds_on if the LLM found it
    specs = data.get("specs", {})
    rdson_hs = _float_or_none(specs.get("rdson_hs_mohm"))
    rdson_ls = _float_or_none(specs.get("rdson_ls_mohm"))
    if rdson_hs and state.ref_spec and not state.ref_spec.rdson_hs:
        old = state.ref_spec
        state.ref_spec = ReferenceSpec(
            topology=old.topology, vin_min=old.vin_min, vin_nom=old.vin_nom,
            vin_max=old.vin_max, vout=old.vout, iout=old.iout, pout=old.pout,
            fsw=old.fsw, efficiency_target=old.efficiency_target,
            isolation_required=old.isolation_required,
            turns_ratio=old.turns_ratio,
            rdson_hs=rdson_hs, rdson_ls=rdson_ls or rdson_hs,
            extra=old.extra,
        )
        logger.info("CRE stage 2.7: extracted Rds_on from PDF: HS=%.1fmΩ LS=%.1fmΩ",
                     rdson_hs, rdson_ls or rdson_hs)

    logger.info("CRE stage 2.7: extracted %d efficiency points, ripple=%s mV",
                 len(eff_dict), state.ref_claims.vout_ripple_mv)
    return state


def _stage2_8_testbench(state: CREState) -> CREState:
    """Run the virtual test bench: rebuild and simulate the reference converter."""
    from heaviside.pipeline.cre_testbench import run_testbench
    return run_testbench(state)


def run_cre_pipeline(
    reference: str,
    *,
    pdf_path: Path | None = None,
    verbose: bool = False,
) -> CREOutcome:
    """Run the full CRE pipeline end-to-end.

    Returns a ``CREOutcome`` with the reference spec, extracted BOM,
    design outcome, review verdicts, and pass/fail status.
    """
    state = CREState(reference=reference, pdf_path=pdf_path)

    state = _stage0_extract_pdf(state)
    state = _stage1_competitor(state)
    state = _stage2_reverse_engineer(state)
    state = _stage2_5_verify_mpns(state)
    state = _stage2_65_extract_rdson(state)
    state = _stage2_7_extract_claims(state)
    state = _stage2_8_testbench(state)

    # Stage 3 (competing design) only if testbench passed or was skipped
    if not state.passed and state.ref_spec and state.ref_spec.vout > 0:
        state = _stage3_design(state)
    state = _stage4_review(state)

    outcome = CREOutcome.from_state(state)
    logger.info("CRE pipeline %s: %s",
                 "PASSED" if outcome.passed else "FAILED",
                 reference)
    return outcome


__all__ = ["CREPipelineError", "run_cre_pipeline"]
