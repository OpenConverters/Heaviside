# Heaviside as an MCP App

Exposes the Heaviside design pipeline as [MCP](https://modelcontextprotocol.io) tools over
**stdio or streamable HTTP**, with a results widget served as an
[MCP Apps](https://modelcontextprotocol.io/extensions/apps/build) (SEP-1865) UI resource.

The server itself lives at `heaviside/mcp_server.py` (it is part of the package, and the CLI
starts it); this directory is the widget build.

## Why the HTTP transport exists

Moebius registers pipelines **by URL** — one process per pipeline, health-checked and
reconnectable. A stdio server has to be spawned as a subprocess per session, which does not fit
that model (ABT #667). Both transports now serve the same tool surface from the same object:

```bash
heaviside serve --mcp                  # stdio
heaviside serve --mcp --http           # streamable HTTP on 127.0.0.1:8405/mcp
python -m heaviside.mcp_server [--http]
```

Ports in the house sequence: Hertz 8400, Kirchhoff 8401, Kelvin 8402, Moebius bridge 8404,
Heaviside 8405. Override with `HEAVISIDE_MCP_PORT` / `--port`.

| Variable | Meaning |
|---|---|
| `HEAVISIDE_MCP_PORT` / `HEAVISIDE_MCP_HOST` | where the HTTP transport binds |
| `HEAVISIDE_PUBLIC_HOST` / `HEAVISIDE_ALLOW_ANY_HOST` / `HEAVISIDE_ALLOWED_ORIGINS` | tunnel allowlisting |
| `KIRCHHOFF_BUILD` | where `PyKirchhoff` was built (needed by `design_magnetic`/`design_bom`) |
| `HEAVISIDE_TAS_DATA_DIR` | the TAS catalogue Kelvin selects from |

> The SDK's DNS-rebinding protection rejects an unrecognised `Host` with a bare
> `421 Invalid Host header`. Behind a tunnel the Host is the *public* name, so every request
> 421s — and a host that cannot speak MCP typically falls back to probing for OAuth, surfacing
> as "couldn't register with the sign-in service". Name the public host in
> `HEAVISIDE_PUBLIC_HOST` before blaming authentication.

## Tools

| Tool | Returns | Widget |
|---|---|---|
| `list_topologies` | every registered converter topology, by family | — |
| `design_magnetic` | ranked magnetic designs (core, material, turns, losses, Lm) | results |
| `design_bom` | one line per component, sourced or not | results |
| `cross_reference` | a substitute per BOM line, with status and diagnostics | results |
| `reverse_engineer` | a reference design's spec + BOM, and how ours compares | — |
| `query_lessons` | the teacher's lesson store | — |

Results travel on two channels: a compact digest for the model in `content`, and the full
payload in `structuredContent` for the widget. The previous server returned one big
`json.dumps` blob as text — that puts an entire BOM into the context window and leaves a widget
nothing to render.

Errors propagate as MCP errors. The previous server caught them and returned
`{"error": "..."}` as a successful result, which reads to a model exactly like an answer.

## What `design_bom` will tell you that it used to hide

Every component of the design gets a line, **filled or not**. Returning only the sourced ones
made a BOM that sourced 1 of 6 read like a complete 1-line BOM.

Right now a Kirchhoff-built TAS sources exactly its synthesised auxiliaries (Cin, feedback
divider, …) and nothing else: `assemble_bom_from_tas` recognises a placeholder by
`component["data"]` being a **string** containing `mosfets.ndjson`, while a Kirchhoff TAS
carries `data` as an object (`{"semiconductor": …, "inputs": …}`). No placeholder matches, so
Q1/D1/Cout are never looked at — and "not looked at" used to report identically to "no line
needed". That is **ABT #681**, not something this server should paper over; the tool now says
so out loud.

## The widget

`ui://heaviside/results.html` — one table with per-row detail and a "use this" action that
reports the choice back to the model through `updateModelContext`. It serves all three
list-returning tools, because ranked magnetic designs, BOM lines and cross-referenced
substitutes are the same shape and the same decision. Built from `src/results.js` with
`@modelcontextprotocol/ext-apps` and bundled single-file by `vite-plugin-singlefile`, because
MCP App resources render in a deny-by-default CSP iframe.

```bash
cd mcp && npm install && npm run build      # -> mcp/dist/results.html
```

`assert_widgets_resolve()` runs before either transport starts and refuses to serve if a
registered `ui://` has no bundle behind it. OpenMagnetics ships a curves URI with no bundle and
no build tooling, so eight of its tools have advertised an unfetchable chart ever since and
nothing complained (ABT #651).

The resource reads `dist/` per request and hosts fetch it per mount, so editing the widget is
`npm run build` and nothing else — no server restart.

**ABT #663** asks websharedcomponents for a shared ranked-candidate component across
Kirchhoff, OpenMagnetics, Hertz and Kelvin. This widget and Kelvin's picker are both
deliberately disposable: when that lands, both should adopt it.

## Testing

```bash
KIRCHHOFF_BUILD=… PYTHONPATH=…/Kelvin/build HEAVISIDE_TAS_DATA_DIR=… \
  python3 mcp/smoke.py [--skip-design] [--skip-http]
```

Calls every tool, runs a **real** magnetic design through PyOpenMagnetics (a buck inductor:
P 18/11 / 3F36, 11 turns, 20.2 µH against a 22 µH ask), sources a real BOM, then starts the
HTTP transport in a subprocess and drives it with a real MCP client — tool list, `ui://` read,
and a tool call. The old server crashed at startup (`Server.run()` missing
`initialization_options`) and nobody noticed, because nothing ever started it. This is what
would have noticed.
