/**
 * Heaviside results widget — the MCP App.
 *
 * Three of Heaviside's tools return the same idea in different clothes: a list of
 * things the engineer has to choose between or check off.
 *
 *   design_magnetic  -> ranked magnetic designs (core, material, turns, losses)
 *   design_bom       -> one sourced line per component
 *   cross_reference  -> one substitute per line, with a status
 *
 * So there is one widget and one payload envelope (`mode` + `candidates[]`) rather
 * than three near-identical tables. The columns come from the mode; everything else
 * — row detail, the choice action, the host bridge — is shared.
 *
 * ABT #663 asks websharedcomponents for a ranked-candidate component covering
 * Kirchhoff, OpenMagnetics, Hertz and Kelvin as well. This widget is deliberately
 * disposable: when that lands, this server should adopt it and delete this file.
 */
import { App } from "@modelcontextprotocol/ext-apps";

const app = new App({ name: "Heaviside results", version: "0.1.0" });

const state = {
  mode: "",
  topology: "",
  rows: [],
  target: null,
  passed: null,
  diagnostics: [],
  selected: null,
  expanded: new Set(),
  error: "",
};

const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "onclick") n.onclick = v;
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
};

/**
 * Engineering notation, matching the server's digest so the two read alike.
 *
 * An absent value is an em dash, never 0: "no core-loss number in this MAS" and
 * "this design dissipates nothing" are different statements.
 */
function eng(v, unit = "") {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  if (v === 0) return `0 ${unit}`.trim();
  const a = Math.abs(v);
  const steps = [[1e-12, "p"], [1e-9, "n"], [1e-6, "µ"], [1e-3, "m"], [1, ""]];
  for (const [f, p] of steps) {
    if (a < f * 1000) return `${trim(v / f)} ${p}${unit}`.trim();
  }
  if (a < 1e6) return `${trim(v / 1e3)} k${unit}`.trim();
  return `${trim(v / 1e6)} M${unit}`.trim();
}
const trim = (x) => String(Number(x.toPrecision(4)));
const plain = (v) => (v === null || v === undefined || v === "" ? "—" : String(v));

/** The identity a row is known by — what "use this" reports and what expands. */
const keyOf = (row, i) => row.ref ?? row.mpn ?? row.label ?? `row_${i}`;

/**
 * Column set per mode. Each column is {label, get}: `get` returns a string, so a
 * missing datum becomes an em dash here rather than "undefined" in the table.
 */
// Column sets keyed by the CONTRACT's modes (ABT #741). `magnetics` and
// `crossref` are gone from this server: a sized magnetic is a `design` (it has
// no MPN, so it is not a candidate), and a whole-BOM re-source is a `bom` (it
// has one original PER LINE, so it is not a crossref).
//
// `design` reads through `properties`, which is where the contract puts a
// design's own quantities — what describes a magnetic does not describe a
// filter, so the branch does not pretend one vocabulary fits both.
const COLUMNS = {
  design: [
    { label: "#", get: (r) => plain(r.rank) },
    { label: "Design", get: (r) => plain(r.label) },
    { label: "Turns", get: (r) =>
        ((r.properties ?? {}).turns ?? []).filter((t) => t != null).join(" + ") || "—" },
    { label: "Core loss", get: (r) => eng((r.properties ?? {}).core_losses_W, "W") },
    { label: "Winding loss", get: (r) => eng((r.properties ?? {}).winding_losses_W, "W") },
    { label: "Score", get: (r) => plain(typeof r.score === "number" ? trim(r.score) : null) },
  ],
  bom: [
    { label: "Ref", get: (r) => plain(r.ref) },
    // Present only when re-sourcing; an em dash on a freshly sourced BOM is
    // correct — there was no previous part.
    { label: "Original", get: (r) => plain(r.originalMpn) },
    { label: "Part", get: (r) => plain(r.mpn) },
    { label: "Manufacturer", get: (r) => plain(r.manufacturer) },
    { label: "Category", get: (r) => plain(r.kind) },
  ],
};

const TITLE = {
  design: "Designs",
  bom: "Bill of materials",
};

/** Fields already shown as columns or carried for the pipeline, not detail rows. */
const SHOWN = new Set([
  "rank", "label", "score", "properties", "document", "notes",
  "ref", "kind", "mpn", "manufacturer", "originalMpn", "status", "value", "unit",
]);

// The contract's closed set: exact | recommended | partial | no_substitute |
// unsourced. `unsourced` is amber rather than red on purpose — it does NOT mean
// "nothing fits", it means this line was never looked at, and colouring it like
// a failure would assert a negative result nobody established.
const STATUS_CLASS = {
  exact: "pass", recommended: "pass",
  partial: "warn", unsourced: "warn",
  no_substitute: "fail",
};
function statusClass(status) {
  return STATUS_CLASS[String(status ?? "")] ?? "";
}

function detailPanel(row) {
  const kids = [];
  if (Array.isArray(row.windings) && row.windings.length) {
    kids.push(el("div", { class: "chips" }, row.windings.map((w) =>
      el("span", { class: "chip" }, `${plain(w.name)}: ${plain(w.turns)} turns`))));
  }
  if (typeof row.magnetizing_inductance_H === "number") {
    kids.push(el("div", { class: "chips" },
      el("span", { class: "chip" }, `Lm ${eng(row.magnetizing_inductance_H, "H")}`)));
  }
  // Whatever else the pipeline attached to this line (selection provenance, margins,
  // reasons) — shown verbatim rather than dropped, since it is the evidence for the pick.
  const extra = Object.entries(row).filter(([k, v]) =>
    !SHOWN.has(k) && !k.startsWith("_") && v !== null && v !== undefined && v !== "" &&
    typeof v !== "object");
  if (extra.length) {
    kids.push(el("div", { class: "chips" }, extra.map(([k, v]) =>
      el("span", { class: "chip" }, `${k}: ${plain(v)}`))));
  }
  if (!kids.length) kids.push(el("div", { class: "muted" }, "No further detail recorded."));
  return el("td", { class: "detail", colspan: "99" }, kids);
}

function render() {
  const root = document.getElementById("app");
  root.textContent = "";

  if (state.error) {
    root.append(el("div", { class: "err" }, state.error));
    return;
  }
  if (!state.rows.length) {
    root.append(el("div", { class: "muted pad" }, "Waiting for results…"));
    return;
  }

  const cols = COLUMNS[state.mode] ?? COLUMNS.bom;
  const sub = [
    state.topology,
    state.target ? `to ${state.target}` : null,
    `${state.rows.length} line${state.rows.length === 1 ? "" : "s"}`,
    state.passed === null || state.passed === undefined
      ? null : state.passed ? "passed" : "did not pass",
  ].filter(Boolean).join(" · ");
  root.append(el("div", { class: "head" },
    el("h1", {}, TITLE[state.mode] ?? "Results"),
    el("div", { class: "sub" }, sub)));

  const head = el("tr", {},
    cols.map((c) => el("th", {}, c.label)),
    // Every BOM line has a status and it is the most important thing on the
    // row; a design has none — the engine produced it, so there is no verdict
    // to report and an empty column would invite one to be invented.
    state.mode === "bom" ? el("th", {}, "Status") : null,
    el("th", { class: "act" }, ""));

  const body = [];
  state.rows.forEach((row, i) => {
    const key = keyOf(row, i);
    const chosen = state.selected === key;
    body.push(el("tr", { class: `row${chosen ? " chosen" : ""}`, onclick: () => toggle(key) },
      cols.map((c) => el("td", {}, c.get(row))),
      state.mode === "bom"
        ? el("td", { class: `status ${statusClass(row.status)}` }, plain(row.status))
        : null,
      el("td", { class: "act" },
        el("button", {
          class: `btn${chosen ? " chosen" : ""}`,
          onclick: (e) => { e.stopPropagation(); choose(row, key); },
        }, chosen ? "chosen" : "use this"))));
    if (state.expanded.has(key)) {
      body.push(el("tr", { class: "detailrow" }, detailPanel(row)));
    }
  });

  root.append(el("table", { class: "tbl" },
    el("thead", {}, head), el("tbody", {}, body)));

  if (state.diagnostics.length) {
    root.append(el("ul", { class: "diags" },
      state.diagnostics.slice(0, 8).map((d) => el("li", {}, d))));
  }
  root.append(el("div", { class: "hint" },
    "Click a row for the detail behind it. “Use this” tells the assistant your choice."));
}

function toggle(key) {
  if (state.expanded.has(key)) state.expanded.delete(key);
  else state.expanded.add(key);
  render();
}

/**
 * Report the user's choice to the model.
 *
 * updateModelContext OVERWRITES rather than appends, so the message restates what
 * was being chosen and from what — otherwise the model receives a part number with
 * no idea which decision it answers.
 */
async function choose(row, key) {
  state.selected = key;
  render();
  const what = state.mode === "design"
    ? `design for the ${state.topology || "converter"}`
    : row.originalMpn
      // A re-sourced line and a freshly sourced one are different decisions,
      // and the model needs to know which it is being told about.
      ? `substitute for ${row.originalMpn} on ${row.ref ?? "this line"}`
      : `part for ${row.ref ?? "this line"}`;
  const label = state.mode === "design"
    ? plain(row.label)
    : plain(row.mpn ?? row.ref);
  const lines = [`[user selected] ${label} as the ${what}.`];
  const cols = COLUMNS[state.mode] ?? COLUMNS.bom;
  lines.push("[detail] " + cols.map((c) => `${c.label} ${c.get(row)}`).join(", "));
  if (row.status) lines.push(`[status] ${row.status}`);
  await app.updateModelContext({
    content: [{ type: "text", text: lines.join("\n") }],
    // The full engineering document is deliberately NOT sent back: it is
    // megabytes, and the model can ask for it by rank. What travels is the
    // identity of the choice. (`document` is the contract's name for it; it
    // was `mas` before, which named one engine's format for a general idea.)
    structuredContent: JSON.parse(JSON.stringify({
      selected: { ...row, document: undefined },
      context: { mode: state.mode, topology: state.topology, target: state.target },
    })),
  });
}

/**
 * Read a tool result.
 *
 * `candidates` is the envelope Heaviside and Kelvin both normalise to; `rows` and
 * `bom` are accepted so a payload from another server still renders rather than
 * showing "waiting for results" at data that is right there.
 */
function ingest(sc) {
  state.error = "";
  // A finished job carries its real result nested (contract `mode: "job"`), so
  // unwrap it: the widget should render the outcome, not the envelope that
  // delivered it. submit_crossref -> job_result is now the normal path.
  if (sc.mode === "job" && sc.result) sc = sc.result;
  state.error = "";
  state.mode = sc.mode ?? "";
  state.topology = sc.topology ?? "";
  // ONE name per shape, no aliases (ABT #741). `designs` and `lines` are what
  // the contract calls them; accepting `candidates`/`rows`/`bom` here would
  // hide exactly the drift the contract exists to catch.
  state.rows = (sc.mode === "design" ? sc.designs : sc.lines) ?? [];
  if (!Array.isArray(state.rows)) state.rows = [];
  state.target = sc.targetManufacturer ?? null;
  state.passed = sc.passed ?? null;
  state.diagnostics = Array.isArray(sc.diagnostics) ? sc.diagnostics : [];
  state.selected = null;
  state.expanded = new Set();
  render();
}

app.ontoolresult = async (result) => {
  const sc = result?.structuredContent;
  if (!sc) {
    state.error = "The tool returned no structured content for this widget.";
    render();
    return;
  }
  ingest(sc);
};

render();
await app.connect();
