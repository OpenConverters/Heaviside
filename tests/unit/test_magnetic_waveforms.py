"""magnetic_waveforms — extract PyOM's ngspice excitation traces from the MAS."""
from __future__ import annotations

from heaviside.pipeline.converter_designer import magnetic_waveforms


def _mas(n=2048):
    t = [k * 1e-8 for k in range(n)]
    cur = [2.0 + 0.3 * (k % 100) / 100 for k in range(n)]
    volt = [12.0 if k % 100 < 20 else -3.3 for k in range(n)]
    return {"inputs": {"operatingPoints": [
        {"name": "nom", "excitationsPerWinding": [
            {"current": {"waveform": {"time": t, "data": cur}},
             "voltage": {"waveform": {"time": t, "data": volt}}}]},
        {"excitationsPerWinding": [
            {"current": {"waveform": {"time": t, "data": cur}}}]},  # no voltage, no name
    ]}}


def test_extracts_and_downsamples_per_op():
    wf = magnetic_waveforms(_mas(2048), max_points=400)
    assert len(wf) == 2
    op0 = wf[0]
    assert op0["op_index"] == 0 and op0["label"] == "nom"
    assert len(op0["time_s"]) <= 400 and len(op0["current_a"]) == len(op0["time_s"])
    assert op0["voltage_v"] is not None and len(op0["voltage_v"]) == len(op0["time_s"])
    # second OP has no voltage / no name -> graceful
    assert wf[1]["label"] == "op1" and wf[1]["voltage_v"] is None


def test_no_downsample_when_small():
    wf = magnetic_waveforms(_mas(50), max_points=400)
    assert len(wf[0]["time_s"]) == 50


def test_skips_ops_without_waveform():
    mas = {"inputs": {"operatingPoints": [
        {"excitationsPerWinding": []},                       # no excitation
        {"excitationsPerWinding": [{"current": {}}]},        # no waveform
    ]}}
    assert magnetic_waveforms(mas) == []


def test_empty_on_malformed():
    assert magnetic_waveforms({}) == []
    assert magnetic_waveforms({"inputs": {}}) == []
    assert magnetic_waveforms("nope") == []
