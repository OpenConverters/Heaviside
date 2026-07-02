"""Connector + analog-IC cross-reference: identity gates, param verdicts and
the connector mating-system check.

Connectors are IDENTITY-matched (family/positions/gender/pitch are hard
gates; ratings are ≥/≤ checks) and carry a mating-system verdict — a
connector is half of a mated pair, so a cross-vendor series swap is only a
drop-in for standardized interfaces, no-mate families (terminal block/card
edge) or commodity pin headers at matching pitch. Analog ICs gate on
function (subtype) and channel count. All fixtures are synthetic envelopes;
no LLM, no catalogue files.
"""

from __future__ import annotations

from heaviside.pipeline.crossref_pipeline import (
    _analog_attrs,
    _connector_attrs,
    _rank_analog_candidates,
    _rank_connector_candidates,
    _rank_timebase_candidates,
    _summarize_candidate,
    _timebase_attrs,
)
from heaviside.pipeline.param_check import (
    FAIL,
    PASS,
    UNVERIFIED,
    WARN,
    connector_mating_check,
    evaluate_params,
)

# ── fixtures ─────────────────────────────────────────────────────────────────


def _conn_env(
    mpn: str,
    *,
    mfr: str = "Würth Elektronik",
    family: str = "pinHeaderSocket",
    positions: int = 6,
    pitch: float | None = 0.00254,
    polarity: str | None = "male",
    current: float = 3.0,
    voltage: float = 250.0,
    series: str | None = "WR-PHD",
    interface_standard: str | None = None,
    mounting: str | None = "tht",
    temp: tuple[float, float] = (-40.0, 105.0),
    description: str = "",
) -> dict:
    mech: dict = {"positions": positions}
    if pitch is not None:
        mech["pitch"] = pitch
    if mounting is not None:
        mech["mountingStyle"] = mounting
    part: dict = {"partNumber": mpn}
    if polarity is not None:
        part["matingPolarity"] = polarity
    if series is not None:
        part["series"] = series
    fd: dict = {"family": family}
    if interface_standard is not None:
        fd["interfaceStandard"] = interface_standard
    return {
        "connector": {
            "manufacturerInfo": {
                "name": mfr,
                "reference": mpn,
                "status": "production",
                "description": description,
                "datasheetInfo": {
                    "part": part,
                    "familyDetails": fd,
                    "mechanical": mech,
                    "electrical": {
                        "ratedCurrentPerContact": current,
                        "ratedVoltage": voltage,
                    },
                    "environmental": {
                        "operatingTemperature": {"minimum": temp[0], "maximum": temp[1]}
                    },
                },
            }
        }
    }


def _analog_env(
    mpn: str,
    *,
    subtype: str = "operationalAmplifier",
    channels: int = 2,
    supply: tuple[float, float] = (1.8, 5.5),
    gbw: float | None = 1e6,
    vos: float | None = 1e-4,
    package: str = "SOIC",
    rr_in: bool = True,
    rr_out: bool = True,
    output_stage: str | None = None,
) -> dict:
    elec: dict = {
        "numberOfChannels": channels,
        "railToRailInput": rr_in,
        "railToRailOutput": rr_out,
        "supply": {
            "minimumSupplyVoltage": supply[0],
            "maximumSupplyVoltage": supply[1],
        },
    }
    if gbw is not None:
        elec["gainBandwidthProduct"] = gbw
    if vos is not None:
        elec["inputOffsetVoltage"] = vos
    if output_stage is not None:
        elec["outputStage"] = output_stage
    return {
        "analog": {
            subtype: {
                "manufacturerInfo": {
                    "name": "Texas Instruments",
                    "reference": mpn,
                    "status": "production",
                    "datasheetInfo": {
                        "part": {"partNumber": mpn, "package": package},
                        "electrical": elec,
                    },
                }
            }
        }
    }


def _verdict(results: list[dict], key: str) -> str | None:
    return next((r["verdict"] for r in results if r["name"] == key), None)


# ── connector identity gates in the ranker ──────────────────────────────────


def test_connector_ranker_drops_wrong_positions_gender_pitch() -> None:
    orig = _conn_env("ORIG", mfr="Molex", positions=6, polarity="male", pitch=0.00254)
    comp = {"ref_des": "J1", "component_type": "connector", "_source_env": orig}
    cands = [
        _conn_env("W-9POS", positions=9),
        _conn_env("W-FEMALE", polarity="female"),
        _conn_env("W-2MM", pitch=0.002),
        _conn_env("W-TERM", family="terminalBlock"),
        _conn_env("W-GOOD"),
    ]
    ranked = _rank_connector_candidates(comp, cands, 10)
    mpns = [_summarize_candidate(c, "connector")["mpn"] for c in ranked]
    assert mpns == ["W-GOOD"]


def test_connector_ranker_header_family_straddles_board_to_board() -> None:
    # Molex files 2.54 mm headers as pinHeaderSocket; Würth files the same
    # commodity headers as boardToBoard — the family gate must not kill it.
    orig = _conn_env("ORIG", mfr="Molex", family="pinHeaderSocket")
    comp = {"ref_des": "J1", "component_type": "connector", "_source_env": orig}
    ranked = _rank_connector_candidates(comp, [_conn_env("W-B2B", family="boardToBoard")], 10)
    assert len(ranked) == 1


def test_connector_ranker_underrated_current_ranks_last() -> None:
    orig = _conn_env("ORIG", mfr="Molex", current=3.0)
    comp = {"ref_des": "J1", "component_type": "connector", "_source_env": orig}
    ranked = _rank_connector_candidates(
        comp, [_conn_env("W-WEAK", current=1.0), _conn_env("W-OK", current=3.0)], 10
    )
    mpns = [_summarize_candidate(c, "connector")["mpn"] for c in ranked]
    assert mpns == ["W-OK", "W-WEAK"]


def test_connector_ranker_unknown_original_returns_nothing() -> None:
    # Nothing known about the original (not in DB, no parsable text): offering
    # arbitrary candidates would invite a plausible-looking wrong pick.
    comp = {"ref_des": "J1", "component_type": "connector", "original_mpn": "999"}
    assert _rank_connector_candidates(comp, [_conn_env("W-GOOD")], 10) == []


def test_connector_attrs_backfills_pitch_from_description() -> None:
    env = _conn_env(
        "ORIG",
        pitch=None,
        description="2.54mm Pitch C-Grid Breakaway Header, Dual Row, Vertical",
    )
    assert abs(_connector_attrs(env)["pitch"] - 0.00254) < 1e-9


def test_connector_text_fallback_gates_on_bom_description() -> None:
    comp = {
        "ref_des": "J1",
        "component_type": "connector",
        "description": "Header, 2.54mm pitch, 10POS, vertical",
    }
    cands = [_conn_env("W-6POS", positions=6), _conn_env("W-10POS", positions=10)]
    ranked = _rank_connector_candidates(comp, cands, 10)
    mpns = [_summarize_candidate(c, "connector")["mpn"] for c in ranked]
    assert mpns == ["W-10POS"]


# ── connector param verdicts ─────────────────────────────────────────────────


def test_connector_params_pass_and_fail() -> None:
    o = _summarize_candidate(_conn_env("O", mfr="Molex"), "connector")
    good = _summarize_candidate(_conn_env("S"), "connector")
    res = evaluate_params("connector", o, good)
    assert _verdict(res, "positions") == PASS
    assert _verdict(res, "pitch_mm") == PASS
    assert _verdict(res, "polarity") == PASS

    worse = _summarize_candidate(
        _conn_env("S2", positions=9, current=1.0, temp=(-20.0, 85.0)), "connector"
    )
    res = evaluate_params("connector", o, worse)
    assert _verdict(res, "positions") == FAIL
    assert _verdict(res, "rated_current_A") == FAIL
    assert _verdict(res, "temp_min_C") == FAIL  # −20 covers less than −40, beyond 15 °C
    assert _verdict(res, "temp_max_C") == FAIL  # 85 vs 105 is a 20 °C shortfall, beyond 15 °C


def test_connector_unverified_identity_params_are_critical() -> None:
    o = _summarize_candidate(_conn_env("O", mfr="Molex", pitch=None), "connector")
    s = _summarize_candidate(_conn_env("S"), "connector")
    res = evaluate_params("connector", o, s)
    pitch = next(r for r in res if r["name"] == "pitch_mm")
    assert pitch["verdict"] == UNVERIFIED
    assert pitch["critical"] is True  # blocks a clean 'recommended'


# ── mating-system check ──────────────────────────────────────────────────────


def test_mating_same_series_passes() -> None:
    o = _summarize_candidate(_conn_env("O", series="WR-PHD"), "connector")
    s = _summarize_candidate(_conn_env("S", series="WR-PHD"), "connector")
    assert connector_mating_check(o, s)["verdict"] == PASS


def test_mating_terminal_block_has_no_mating_half() -> None:
    o = _summarize_candidate(
        _conn_env("O", mfr="Molex", family="terminalBlock", series="Eurostyle"), "connector"
    )
    s = _summarize_candidate(_conn_env("S", family="terminalBlock", series="WR-TBL"), "connector")
    assert connector_mating_check(o, s)["verdict"] == PASS


def test_mating_standardized_interface_passes() -> None:
    o = _summarize_candidate(
        _conn_env("O", mfr="Molex", family="dataInterface", series="X", interface_standard="RJ45"),
        "connector",
    )
    s = _summarize_candidate(
        _conn_env("S", family="dataInterface", series="WR-MJ", interface_standard="RJ45"),
        "connector",
    )
    assert connector_mating_check(o, s)["verdict"] == PASS


def test_mating_cross_series_proprietary_fails() -> None:
    o = _summarize_candidate(
        _conn_env("O", mfr="Molex", family="wireToBoard", series="Micro-Fit"), "connector"
    )
    s = _summarize_candidate(_conn_env("S", family="wireToBoard", series="WR-WTB"), "connector")
    row = connector_mating_check(o, s)
    assert row["verdict"] == FAIL
    assert "matched set" in row["note"]


def test_mating_header_cross_classified_warns() -> None:
    o = _summarize_candidate(
        _conn_env("O", mfr="Molex", family="pinHeaderSocket", series="C-Grid"), "connector"
    )
    s = _summarize_candidate(_conn_env("S", family="boardToBoard", series="WR-PHD"), "connector")
    row = connector_mating_check(o, s)
    assert row["verdict"] == WARN


def test_mating_unknown_original_is_critical_unverified() -> None:
    s = _summarize_candidate(_conn_env("S"), "connector")
    row = connector_mating_check(None, s)
    assert row["verdict"] == UNVERIFIED
    assert row["critical"] is True


# ── analog identity gates + params ───────────────────────────────────────────


def test_analog_ranker_gates_subtype_and_channels() -> None:
    orig = _analog_env("ORIG", subtype="operationalAmplifier", channels=2)
    comp = {"ref_des": "U1", "component_type": "analog", "_source_env": orig}
    cands = [
        _analog_env("CMP", subtype="comparator", channels=2),
        _analog_env("QUAD", channels=4),
        _analog_env("DUAL", channels=2),
    ]
    ranked = _rank_analog_candidates(comp, cands, 10)
    mpns = [_summarize_candidate(c, "analog")["mpn"] for c in ranked]
    assert mpns == ["DUAL"]


def test_analog_ranker_text_fallback_infers_function() -> None:
    comp = {
        "ref_des": "U1",
        "component_type": "analog",
        "description": "IC OPAMP GP dual RRIO 8SOIC",
    }
    cands = [
        _analog_env("CMP", subtype="comparator"),
        _analog_env("OPA", subtype="operationalAmplifier", channels=2),
    ]
    ranked = _rank_analog_candidates(comp, cands, 10)
    mpns = [_summarize_candidate(c, "analog")["mpn"] for c in ranked]
    assert mpns == ["OPA"]


def test_analog_params_flag_downgrades() -> None:
    o = _summarize_candidate(_analog_env("O", gbw=1e6, supply=(1.8, 36.0)), "analog")
    s = _summarize_candidate(
        _analog_env("S", gbw=1e5, supply=(2.7, 5.5), rr_in=False, package="DSBGA"), "analog"
    )
    res = evaluate_params("analog", o, s)
    assert _verdict(res, "subtype") == PASS
    assert _verdict(res, "channels") == PASS
    assert _verdict(res, "gbw") == FAIL  # 10× slower
    assert _verdict(res, "supply_max_V") == FAIL  # 5.5 V part on a 36 V original
    assert _verdict(res, "supply_min_V") == FAIL  # needs 2.7 V, original ran at 1.8 V
    assert _verdict(res, "rail_to_rail_input") == FAIL  # RRI downgrade
    assert _verdict(res, "package") == FAIL  # not a drop-in


def test_analog_comparator_output_stage_must_match() -> None:
    o = _summarize_candidate(
        _analog_env("O", subtype="comparator", output_stage="pushPull"), "analog"
    )
    s = _summarize_candidate(
        _analog_env("S", subtype="comparator", output_stage="openDrain"), "analog"
    )
    assert _verdict(evaluate_params("analog", o, s), "output_stage") == FAIL


def test_analog_attrs_reads_subtype_dynamically() -> None:
    attrs = _analog_attrs(_analog_env("X", subtype="adc", channels=8, gbw=None, vos=None))
    assert attrs["subtype"] == "adc"
    assert attrs["channels"] == 8


# ── time bases (TBAS: crystals / oscillators) ────────────────────────────────


def _tb_env(
    mpn: str,
    *,
    mfr: str = "Würth Elektronik",
    technology: str = "quartzCrystal",
    frequency: float = 32768.0,
    tolerance: float | None = 2e-5,
    load_capacitance: float | None = 1.25e-11,
    esr: float | None = 70e3,
    output_type: str | None = "none",
    temp: tuple[float, float] = (-40.0, 85.0),
    with_inputs: bool = True,
) -> dict:
    elec: dict = {"technology": technology, "frequency": frequency}
    if tolerance is not None:
        elec["frequencyTolerance"] = tolerance
    if load_capacitance is not None:
        elec["loadCapacitance"] = load_capacitance
    if esr is not None:
        elec["equivalentSeriesResistance"] = esr
    if output_type is not None:
        elec["outputType"] = output_type
    doc: dict = {
        "oscillator": {
            "manufacturerInfo": {
                "name": mfr,
                "reference": mpn,
                "status": "production",
                "datasheetInfo": {
                    "part": {"partNumber": mpn, "package": "3215"},
                    "electrical": elec,
                    "thermal": {
                        "operatingTemperature": {"minimum": temp[0], "maximum": temp[1]}
                    },
                },
            }
        }
    }
    if with_inputs:
        # TBAS documents may carry inputs BEFORE the family key — the subtype
        # descent must skip it, not give up at the first sibling.
        doc = {"inputs": {"designRequirements": {"name": mpn}}, **doc}
    return {"timeBase": doc}


def test_timebase_attrs_skip_inputs_sibling() -> None:
    attrs = _timebase_attrs(_tb_env("X1", with_inputs=True))
    assert attrs["subtype"] == "oscillator"
    assert attrs["frequency"] == 32768.0
    assert attrs["technology"] == "quartzCrystal"


def test_timebase_ranker_gates_frequency_technology_cl() -> None:
    orig = _tb_env("ORIG", mfr="Abracon")
    comp = {"ref_des": "Y1", "component_type": "timeBase", "_source_env": orig}
    cands = [
        _tb_env("W-25M", frequency=25e6),  # wrong frequency
        _tb_env("W-MEMS", technology="mems"),  # wrong technology
        _tb_env("W-CL18", load_capacitance=1.8e-11),  # wrong load capacitance
        _tb_env("W-GOOD"),
    ]
    ranked = _rank_timebase_candidates(comp, cands, 10)
    mpns = [_summarize_candidate(c, "timeBase")["mpn"] for c in ranked]
    assert mpns == ["W-GOOD"]


def test_timebase_ranker_text_fallback() -> None:
    comp = {
        "ref_des": "Y1",
        "component_type": "timeBase",
        "description": "CRYSTAL 32.768KHZ 12.5PF SMD",
    }
    cands = [_tb_env("W-25M", frequency=25e6), _tb_env("W-32K", frequency=32768.0)]
    ranked = _rank_timebase_candidates(comp, cands, 10)
    mpns = [_summarize_candidate(c, "timeBase")["mpn"] for c in ranked]
    assert mpns == ["W-32K"]


def test_timebase_params_verdicts() -> None:
    o = _summarize_candidate(_tb_env("O", mfr="Abracon", tolerance=2e-5, esr=50e3), "timeBase")
    good = _summarize_candidate(_tb_env("S", tolerance=1e-5, esr=40e3), "timeBase")
    res = evaluate_params("timeBase", o, good)
    assert _verdict(res, "frequency") == PASS
    assert _verdict(res, "technology") == PASS
    assert _verdict(res, "load_capacitance_pF") == PASS
    assert _verdict(res, "tolerance_ppm") == PASS
    assert _verdict(res, "esr") == PASS

    worse = _summarize_candidate(
        _tb_env("S2", frequency=32768.0, tolerance=1e-4, esr=200e3, load_capacitance=1.8e-11),
        "timeBase",
    )
    res = evaluate_params("timeBase", o, worse)
    assert _verdict(res, "load_capacitance_pF") == FAIL  # 18 pF ≠ 12.5 pF
    assert _verdict(res, "tolerance_ppm") == FAIL  # 100 ppm vs 20 ppm, beyond 2×
    assert _verdict(res, "esr") == FAIL  # 200k vs 50k, beyond 1.5×


def test_timebase_unknown_original_returns_nothing() -> None:
    comp = {"ref_des": "Y1", "component_type": "timeBase", "original_mpn": "XYZ"}
    assert _rank_timebase_candidates(comp, [_tb_env("W-GOOD")], 10) == []
