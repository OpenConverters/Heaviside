"""Provenance envelope (master-plan step B1).

Every stamped design number must be auditable via a uniform
``{producer, method, source_ref, inputs_hash}`` envelope; the realism gate
reports a sourceless real part as UNAVAILABLE (origin un-auditable), never
silently trusts it.
"""

from __future__ import annotations

from heaviside import provenance

# ---------------------------------------------------------------------------
# inputs_hash — deterministic + order-independent
# ---------------------------------------------------------------------------


def test_inputs_hash_is_deterministic_and_order_independent():
    a = provenance.inputs_hash({"vds_min": 48, "id_min": 4, "tech": ["Si", "GaN"]})
    b = provenance.inputs_hash({"id_min": 4, "tech": ["Si", "GaN"], "vds_min": 48})
    assert a == b  # key order must not matter
    assert len(a) == 16
    # a different input changes the hash
    assert a != provenance.inputs_hash({"vds_min": 60, "id_min": 4, "tech": ["Si", "GaN"]})


def test_inputs_hash_handles_non_json_values():
    # frozenset/enum-ish values serialise via default=str (no exception)
    h = provenance.inputs_hash({"tech": frozenset({"Si"}), "x": object})
    assert isinstance(h, str) and len(h) == 16


# ---------------------------------------------------------------------------
# make / is_complete
# ---------------------------------------------------------------------------


def test_make_produces_complete_envelope():
    env = provenance.make(
        producer="catalogue.select_mosfet",
        method="lowest_qg",
        source_ref="AO4423",
        inputs={"vds_min": 48, "id_min": 4},
    )
    assert set(provenance.REQUIRED_KEYS) <= set(env)
    assert provenance.is_complete(env)


def test_make_rejects_empty_required_fields():
    import pytest

    with pytest.raises(ValueError):
        provenance.make(producer="", method="m", source_ref="x", inputs={})


def test_is_complete_rejects_partial_or_absent():
    assert not provenance.is_complete(None)
    assert not provenance.is_complete({})
    assert not provenance.is_complete(
        {"producer": "p", "method": "m", "source_ref": "s"}
    )  # no hash
    assert not provenance.is_complete(
        {"producer": "p", "method": "m", "source_ref": "s", "inputs_hash": ""}  # empty
    )


# ---------------------------------------------------------------------------
# ensure_selection_canonical — retrofit existing detailed blocks
# ---------------------------------------------------------------------------


def test_ensure_canonical_derives_envelope_from_existing_block():
    block = {
        "category": "mosfet",
        "mpn": "AO4423",
        "manufacturer": "Alpha and Omega",
        "tiebreaker": "lowest_qg",
        "constraints": {"vds_min": 48, "id_min": 4},
        "margins": {"vds": 1.25},
    }
    out = provenance.ensure_selection_canonical(block)
    assert provenance.is_complete(out)
    assert out["producer"] == "catalogue.select_mosfet"
    assert out["method"] == "lowest_qg"
    assert out["source_ref"] == "AO4423"
    # detail preserved (non-destructive retrofit)
    assert out["margins"] == {"vds": 1.25}
    # hash reflects the constraints that drove the pick
    assert out["inputs_hash"] == provenance.inputs_hash({"vds_min": 48, "id_min": 4})


def test_ensure_canonical_is_idempotent():
    block = {"category": "resistor", "mpn": "R1", "sizing": "R=Vth/Ipk"}
    once = provenance.ensure_selection_canonical(block)
    twice = provenance.ensure_selection_canonical(once)
    assert once == twice
    assert provenance.is_complete(twice)


def test_ensure_canonical_returns_none_for_nonmapping():
    assert provenance.ensure_selection_canonical(None) is None
    assert provenance.ensure_selection_canonical("not a dict") is None


def test_stamp_components_walks_tas_and_canonicalises():
    tas = {
        "topology": {
            "stages": [
                {
                    "circuit": {
                        "components": [
                            {
                                "name": "Q1",
                                "mpn": "AO4423",
                                "selection_provenance": {
                                    "category": "mosfet",
                                    "mpn": "AO4423",
                                    "tiebreaker": "lowest_qg",
                                    "constraints": {"vds_min": 48},
                                },
                            },
                            {"name": "R1", "tj_provenance": {"method": "Rth_ja*P"}},
                            {"name": "NODE"},  # nothing to stamp
                        ]
                    }
                },
            ]
        }
    }
    n = provenance.stamp_components(tas)
    assert n == 2
    comps = tas["topology"]["stages"][0]["circuit"]["components"]
    assert provenance.is_complete(comps[0]["selection_provenance"])
    assert provenance.is_complete(comps[1]["tj_provenance"])
    assert "selection_provenance" not in comps[2]


# ---------------------------------------------------------------------------
# realism gate integration — sourceless ⇒ UNAVAILABLE
# ---------------------------------------------------------------------------


def _tas_with_part(prov):
    comp = {"name": "Q1", "mpn": "AO4423"}
    if prov is not None:
        comp["selection_provenance"] = prov
    return {"topology": {"stages": [{"circuit": {"components": [comp]}}]}}


def _provenance_check(report):
    return next(c for c in report.checks if c.name == "selection_provenance_complete")


def test_realism_gate_passes_with_complete_provenance():
    from heaviside.pipeline.realism import CheckStatus, evaluate_tas

    prov = provenance.make(
        producer="catalogue.select_mosfet",
        method="lowest_qg",
        source_ref="AO4423",
        inputs={"vds_min": 48},
    )
    report = evaluate_tas(_tas_with_part(prov), topology="buck")
    assert _provenance_check(report).status is CheckStatus.PASS


def test_realism_gate_unavailable_when_part_is_sourceless():
    from heaviside.pipeline.realism import CheckStatus, evaluate_tas

    # a selected part (has mpn) but no provenance block at all
    report = evaluate_tas(_tas_with_part(None), topology="buck")
    chk = _provenance_check(report)
    assert chk.status is CheckStatus.UNAVAILABLE
    assert "provenance" in (chk.detail or "")


def test_realism_gate_not_applicable_without_selected_parts():
    from heaviside.pipeline.realism import CheckStatus, evaluate_tas

    tas = {"topology": {"stages": [{"circuit": {"components": [{"name": "NODE"}]}}]}}
    assert (
        _provenance_check(evaluate_tas(tas, topology="buck")).status is CheckStatus.NOT_APPLICABLE
    )
