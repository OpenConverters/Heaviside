#!/usr/bin/env python3
"""CRE→Designer→CR triangle: design a converter, then cross-reference its BOM.

For each design:
  1. CRE: extract spec from PDF
  2. full_design(): design a competing converter (power-stage BOM)
  3. Extract designer BOM (Q/D/L/Cout with MPNs + inductance value)
  4. CR: cross-reference the designed BOM to Würth
  5. Report: how many designer components are Würth-addressable

Usage: python scripts/run_triangle.py <design-name>
"""
from __future__ import annotations

import json, logging, os, sys, time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MOONSHOT_API_KEY", "sk-viKudfa58QW8GjUm8aYxkfv5hmz0i5Y3HRdMKKpphPUupleQ")
logging.basicConfig(level=logging.WARNING)

PROTEUS_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs")


def _extract_inductance_henries(comp: dict) -> float | None:
    """Pull the magnetizing inductance (H) from a magnetic component's MAS."""
    data = comp.get("data")
    if not isinstance(data, dict):
        return None
    # MAS: magnetic.inputs.designRequirements.magnetizingInductance, or
    # outputs[].inductance.magnetizingInductance
    try:
        inp = data.get("inputs") or {}
        dr = inp.get("designRequirements") or {}
        mi = dr.get("magnetizingInductance") or {}
        nom = mi.get("nominal")
        if isinstance(nom, (int, float)) and nom > 0:
            return float(nom)
    except (AttributeError, TypeError):
        pass
    return None


def extract_designer_bom(tas: dict) -> list[dict[str, Any]]:
    """Build a CR-format BOM from the designer's TAS power stage."""
    from heaviside.pipeline.realism import _iter_components, _categorise

    bom: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _stage, comp in _iter_components(tas):
        cat = _categorise(comp)
        ref = comp.get("name") or comp.get("ref_des") or "?"
        if ref in seen or cat in ("unknown",):
            continue
        seen.add(ref)
        prov = comp.get("selection_provenance") or {}
        mpn = prov.get("mpn") or comp.get("mpn") or ""
        mfr = prov.get("manufacturer") or comp.get("manufacturer") or ""
        value = ""
        if cat == "magnetic":
            L = _extract_inductance_henries(comp)
            if L:
                value = f"{L*1e6:.2f}uH"
        bom.append({
            "ref_des": ref,
            "category": cat,
            "mpn": mpn,
            "manufacturer": mfr,
            "value": value,
        })
    return bom


def run_one(name: str) -> dict[str, Any]:
    from heaviside.pipeline.cre import CREState
    from heaviside.pipeline.cre_pipeline import (
        _stage0_extract_pdf, _stage1_competitor, _stage2_reverse_engineer,
        _stage2_5_verify_mpns, _stage2_65_extract_rdson, _stage2_7_extract_claims,
    )
    from heaviside.pipeline.full_design import full_design
    from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline
    from heaviside.agents.llm_call import reset_token_usage, get_token_usage

    reset_token_usage()
    t0 = time.time()

    # 1. CRE extract spec
    st = CREState(reference=name, pdf_path=PROTEUS_DIR / f"{name}.pdf")
    st = _stage0_extract_pdf(st)
    st = _stage1_competitor(st)
    st = _stage2_reverse_engineer(st)
    st = _stage2_5_verify_mpns(st)
    st = _stage2_65_extract_rdson(st)
    st = _stage2_7_extract_claims(st)
    if not st.ref_spec:
        return {"name": name, "error": "CRE extraction failed"}

    # 2. Design a competing converter
    spec_dict = st.ref_spec.to_heaviside_spec()
    _, _, outcomes = full_design(spec_dict, n_candidates_per_topology=1, parallel=False)
    if not outcomes or not outcomes[0].tas:
        return {"name": name, "error": "designer produced no BOM"}
    best = outcomes[0]

    # 3. Extract designer BOM
    designer_bom = extract_designer_bom(best.tas)
    addressable = [c for c in designer_bom if c["category"] in ("magnetic", "capacitor", "resistor")]

    # 4. Cross-reference the designed BOM to Würth
    cr_outcome = run_crossref_pipeline(
        designer_bom, "Würth Elektronik",
        circuit_context=f"Designed {best.pick.topology.name} from {name}",
    )
    n_total = len(cr_outcome.components)
    n_found = sum(1 for c in cr_outcome.components
                  if c.status.value in ("recommended", "exact", "partial"))

    elapsed = time.time() - t0
    usage = get_token_usage()
    cost = (usage["input"] * 0.002 + usage["output"] * 0.01) / 1000

    return {
        "name": name,
        "topology": best.pick.topology.name,
        "designer_bom_size": len(designer_bom),
        "addressable": len(addressable),
        "cr_total": n_total,
        "cr_found": n_found,
        "designer_bom": designer_bom,
        "elapsed_s": round(elapsed, 1),
        "cost": round(cost, 2),
    }


def main():
    name = sys.argv[1]
    r = run_one(name)
    print(json.dumps({k: v for k, v in r.items() if k != "designer_bom"}, indent=2))
    if "designer_bom" in r:
        print("\nDesigner BOM:")
        for c in r["designer_bom"]:
            print(f"  {c['ref_des']:8s} {c['category']:12s} {c['mpn'][:24]:24s} {c['value']}")


if __name__ == "__main__":
    main()
