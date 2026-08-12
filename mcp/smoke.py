"""End-to-end smoke test for the Heaviside MCP server — the tools, and both transports.

Not a unit test: it calls the tools the way a host does (through the FastMCP registry, so
the registered schema and the function must agree), runs a real magnetic design through
PyOpenMagnetics, and then starts the HTTP transport and drives it with a real MCP client.
The point is that a broken tool or a broken transport fails HERE — the previous server
crashed on startup with a TypeError and nothing noticed, because nothing ever started it.

    python3 mcp/smoke.py [--skip-design] [--skip-http]

--skip-design leaves out the tools that need PyOpenMagnetics + Kirchhoff (slow, ~30 s).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from heaviside import mcp_server as S            # noqa: E402

SKIP_DESIGN = "--skip-design" in sys.argv
SKIP_HTTP = "--skip-http" in sys.argv
FAILURES: list[str] = []

BUCK_SPEC = {
    "inputVoltage": {"minimum": 36, "maximum": 60, "nominal": 48},
    "desiredInductance": 22e-6,
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.7,
    "efficiency": 0.95,
    "operatingPoints": [
        {
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 200_000,
            "ambientTemperature": 25,
        }
    ],
}


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def text(result) -> str:
    return "\n".join(c.text for c in result.content)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_http(port: int) -> None:
    """Start the streamable-HTTP transport in a subprocess and drive it with a client."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    env = {**os.environ, "HEAVISIDE_MCP_PORT": str(port)}
    proc = subprocess.Popen([sys.executable, "-m", "heaviside.mcp_server", "--http"],
                            cwd=str(_REPO), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if proc.poll() is not None:
                check("the HTTP transport starts", False,
                      f"exited {proc.returncode}: {(proc.stderr.read() or '')[-400:]}")
                return
            with socket.socket() as s:
                s.settimeout(0.5)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.5)
        else:
            check("the HTTP transport starts", False, "never bound its port")
            return

        async def drive() -> tuple[list[str], int, str]:
            async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    names = [t.name for t in (await session.list_tools()).tools]
                    resources = (await session.list_resources()).resources
                    body = (await session.read_resource(resources[0].uri)).contents[0].text
                    out = await session.call_tool("list_topologies", {})
                    return names, len(body), text(out)

        names, widget_len, topologies = asyncio.run(drive())
        check("the HTTP transport serves the whole tool surface", len(names) == 6,
              ", ".join(names))
        check("the widget is served over MCP", widget_len > 10_000, f"{widget_len:,} chars")
        check("a tool call over HTTP returns the engine's answer",
              "topologies" in topologies and "buck" in topologies)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:                       # pragma: no cover
            proc.kill()


def main() -> int:
    print("the registered tool surface")
    tools = asyncio.run(S.mcp.list_tools())
    check("every tool is registered", len(tools) == 6, ", ".join(t.name for t in tools))
    check("every tool has a description", all(t.description for t in tools))
    with_ui = {t.name for t in tools if (t.meta or {}).get("ui/resourceUri")}
    check("the widget is on exactly the tools that return a list to choose from",
          with_ui == {"design_magnetic", "design_bom", "cross_reference"}, ", ".join(sorted(with_ui)))

    print("the MCP Apps widget")
    S.assert_widgets_resolve()
    widget = S.results_widget()
    check("the bundle is self-contained HTML",
          widget.lstrip().startswith("<") and "<script" in widget, f"{len(widget):,} bytes")
    check("no external fetch in the widget (it renders under a deny-by-default CSP)",
          'src="http' not in widget and "src='http" not in widget)

    print("list_topologies")
    r = S.list_topologies()
    payload = r.structuredContent
    check("topologies returned", payload["count"] > 10, f"{payload['count']} topologies")
    check("every entry carries a family",
          all(e.get("family") for e in payload["topologies"]))
    check("the digest names a real topology", "buck" in text(r))

    print("query_lessons")
    r = S.query_lessons(max_age_days=3650)
    check("the lesson store answers", isinstance(r.structuredContent.get("count"), int),
          f"{r.structuredContent.get('count')} lesson(s)")

    if SKIP_DESIGN:
        print("design_magnetic / design_bom: SKIPPED (--skip-design)")
    else:
        print("design_magnetic(buck)  [real PyOpenMagnetics design, ~30 s]")
        r = S.design_magnetic("buck", BUCK_SPEC, max_results=2)
        payload = r.structuredContent
        cands = payload["candidates"]
        check("candidates designed", len(cands) >= 1, f"{len(cands)} candidate(s)")
        check("each candidate names a real core shape and material",
              all(c["core_shape"] and c["core_material"] for c in cands),
              f"{cands[0]['core_shape']} / {cands[0]['core_material']}")
        check("each candidate carries its turns",
              all(any(t for t in c["turns"]) for c in cands),
              f"turns {cands[0]['turns']}")
        # These come out of the MAS `outputs` block, whose nesting is easy to get wrong:
        # a wrong path yields null, which renders as "—" and reads as "the engine did not
        # say" rather than "we looked in the wrong place".
        check("each candidate reports the losses it was ranked on",
              all(isinstance(c["core_losses_W"], float) and isinstance(c["winding_losses_W"], float)
                  for c in cands),
              f"core {cands[0]['core_losses_W']*1e3:.2f} mW, "
              f"winding {cands[0]['winding_losses_W']*1e3:.2f} mW")
        lm = cands[0]["magnetizing_inductance_H"]
        check("each candidate reports its magnetizing inductance",
              all(isinstance(c["magnetizing_inductance_H"], float)
                  and c["magnetizing_inductance_H"] > 0 for c in cands),
              f"{lm * 1e6:.2f} µH (asked for {BUCK_SPEC['desiredInductance'] * 1e6:.0f} µH)"
              if isinstance(lm, float) else f"got {lm!r}")
        check("the payload is the widget's envelope",
              payload["mode"] == "magnetics" and isinstance(payload["candidates"], list))
        check("the MAS is not shipped unless asked", "mas" not in cands[0])
        check("the digest names the core, not just a score", cands[0]["core_shape"] in text(r))

        print("design_bom(buck)  [real Kirchhoff TAS + Kelvin selection]")
        from heaviside.decomposer import kirchhoff_adapter as ka

        tas = ka.design_from_hs_spec("buck", BUCK_SPEC)
        r = S.design_bom("buck", BUCK_SPEC, tas)
        payload = r.structuredContent
        lines = payload["candidates"]
        filled = [l for l in lines if l["filled"]]
        check("every component of the design gets a line, filled or not",
              len(lines) > len(filled) or len(lines) >= 5,
              f"{len(filled)}/{len(lines)} sourced")
        check("every line names a ref des and what kind of part it is",
              all(l.get("ref_des") and l.get("category") for l in lines),
              ", ".join(f"{l['ref_des']}:{l['category']}" for l in lines[:6]))
        check("every sourced line names a real part",
              all(l.get("mpn") for l in filled),
              ", ".join(f"{l['ref_des']}={l['mpn']}" for l in filled[:4]))
        check("the payload is the widget's envelope", payload["mode"] == "bom")
        check("the digest reports the unfilled lines too, not just the sourced one",
              "UNFILLED" in text(r) or len(filled) == len(lines))
        # A margin of +inf ("no constraint, so unbounded headroom") must not reach the
        # host as null, which reads as "the record does not state it".
        margins = [m for line in lines for m in (line.get("margins") or {}).values()]
        check("non-finite margins survive serialisation as a word, not a silent null",
              all(not isinstance(m, float) or (m == m and abs(m) != float("inf"))
                  for m in margins),
              f"{sum(1 for m in margins if m == 'unbounded')} unbounded of {len(margins)}")

        print("design_magnetic with an unsatisfiable topology")
        try:
            S.design_magnetic("not_a_topology", BUCK_SPEC)
            check("an unknown topology is refused", False)
        except Exception as error:                              # noqa: BLE001
            check("an unknown topology is refused loudly, not as a success-shaped error",
                  "not_a_topology" in str(error), type(error).__name__)

    if SKIP_HTTP:
        print("HTTP transport: SKIPPED (--skip-http)")
    else:
        print("the streamable-HTTP transport  [the reason ABT #667 exists]")
        check_http(free_port())

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all smoke checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
