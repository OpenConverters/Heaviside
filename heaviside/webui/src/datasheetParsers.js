// Shared PEAS-envelope → datasheet-view parsers, used by both the catalog
// detail page (CatalogDetail.vue) and the slide-over drawer
// (ComponentDatasheet.vue). One parser per catalog category; parseSheet()
// dispatches on the category and returns null when no view exists, so the
// two consumers can render a common "no datasheet view" fallback.

// ── Value formatters ─────────────────────────────────────────────────────────
export function fmtVal(v, digits = 3) {
  if (v == null) return null
  const abs = Math.abs(v)
  if (abs === 0) return '0'
  if (abs >= 1e9) return `${+(v / 1e9).toPrecision(digits)} G`
  if (abs >= 1e6) return `${+(v / 1e6).toPrecision(digits)} M`
  if (abs >= 1e3) return `${+(v / 1e3).toPrecision(digits)} k`
  if (abs >= 1) return `${+v.toPrecision(digits)} `
  if (abs >= 1e-3) return `${+(v * 1e3).toPrecision(digits)} m`
  if (abs >= 1e-6) return `${+(v * 1e6).toPrecision(digits)} µ`
  if (abs >= 1e-9) return `${+(v * 1e9).toPrecision(digits)} n`
  if (abs >= 1e-12) return `${+(v * 1e12).toPrecision(digits)} p`
  return `${+v.toPrecision(digits)} `
}
export function fmtU(v, unit) { const s = fmtVal(v); return s != null ? `${s}${unit}` : '—' }
export function scalar(v) {
  if (v == null) return null
  if (typeof v === 'number') return v
  if (typeof v === 'object') return v.nominal ?? v.typical ?? v.maximum ?? v.minimum ?? null
  return null
}
export function minTypMax(v) {
  if (v == null) return [null, null, null]
  if (typeof v === 'number') return [null, v, null]
  return [v.minimum ?? null, v.nominal ?? v.typical ?? null, v.maximum ?? null]
}
export function fmtMTM([mn, ty, mx], unit) {
  if (!unit) return { min: mn ?? '—', typ: ty ?? '—', max: mx ?? '—' }
  return {
    min: mn != null ? fmtU(mn, unit) : '—',
    typ: ty != null ? fmtU(ty, unit) : '—',
    max: mx != null ? fmtU(mx, unit) : '—',
  }
}
export function hasAny(mtm, unit) {
  const f = fmtMTM(mtm, unit)
  return f.min !== '—' || f.typ !== '—' || f.max !== '—'
}

// camelCase / kebab-case enum → words, for tags built from schema enums.
function words(s) {
  return s == null ? null : String(s).replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/[-_]/g, ' ').toLowerCase()
}

// ── Per-category parsers ─────────────────────────────────────────────────────
function parseMosfet(env) {
  const mi = env?.semiconductor?.mosfet?.manufacturerInfo ?? {}
  const di = mi.datasheetInfo ?? {}
  const p  = di.part ?? {}
  const el = di.electrical ?? {}
  const th = di.thermal ?? {}
  const me = di.mechanical ?? {}
  return {
    title: mi.reference ?? mi.name ?? '—',
    manufacturer: mi.name ?? '—',
    status: mi.status,
    datasheetUrl: mi.datasheetUrl,
    tags: [p.technology, p.subType, p.case].filter(Boolean),
    headline: [
      { sym: 'V_DS',     label: 'Drain-Source Voltage',     val: fmtU(el.drainSourceVoltage, 'V') },
      { sym: 'R_DS(on)', label: 'On Resistance',            val: fmtU(el.onResistance, 'Ω') },
      { sym: 'I_D',      label: 'Continuous Drain Current', val: fmtU(el.continuousDrainCurrent, 'A') },
    ],
    sections: [
      { title: 'Absolute Maximum Ratings', rows: [
        { param: 'Drain-Source Voltage',     sym: 'V_DS',       mtm: [null, el.drainSourceVoltage, null], unit: 'V' },
        { param: 'Gate-Source Voltage',      sym: 'V_GS',       mtm: [null, el.gateSourceVoltageMax, null], unit: 'V' },
        { param: 'Continuous Drain Current', sym: 'I_D',        mtm: [null, el.continuousDrainCurrent, null], unit: 'A' },
        { param: 'Pulsed Drain Current',     sym: 'I_D(pulse)', mtm: [null, el.pulsedDrainCurrent, null], unit: 'A' },
        { param: 'Power Dissipation',        sym: 'P_D',        mtm: [null, el.powerDissipation, null], unit: 'W' },
      ]},
      { title: 'Static Characteristics', rows: [
        { param: 'On Resistance',              sym: 'R_DS(on)', mtm: minTypMax(el.onResistance), unit: 'Ω', note: el.onResistanceVgs ? `@ Vgs=${el.onResistanceVgs}V, Id=${el.onResistanceId}A` : null },
        { param: 'Gate Threshold Voltage',     sym: 'V_GS(th)', mtm: minTypMax(el.gateThresholdVoltage), unit: 'V' },
        { param: 'Body Diode Forward Voltage', sym: 'V_SD',     mtm: [null, el.bodyDiodeForwardVoltage, null], unit: 'V' },
      ]},
      { title: 'Dynamic Characteristics', rows: [
        { param: 'Input Capacitance',            sym: 'C_iss', mtm: [null, scalar(el.inputCapacitance), null], unit: 'F' },
        { param: 'Output Capacitance',           sym: 'C_oss', mtm: [null, scalar(el.outputCapacitance), null], unit: 'F' },
        { param: 'Reverse Transfer Capacitance', sym: 'C_rss', mtm: [null, scalar(el.reverseTransferCapacitance), null], unit: 'F' },
        { param: 'Total Gate Charge',            sym: 'Q_g',   mtm: [null, scalar(el.totalGateCharge), null], unit: 'C' },
        { param: 'Gate-Source Charge',           sym: 'Q_gs',  mtm: [null, scalar(el.gateSourceCharge), null], unit: 'C' },
        { param: 'Gate-Drain Charge',            sym: 'Q_gd',  mtm: [null, scalar(el.gateDrainCharge), null], unit: 'C' },
        { param: 'Reverse Recovery Charge',      sym: 'Q_rr',  mtm: [null, scalar(el.reverseRecoveryCharge), null], unit: 'C' },
        { param: 'Figure of Merit',              sym: 'FOM',   mtm: [null, scalar(el.figureOfMerit), null], unit: 'Ω·C' },
      ]},
      { title: 'Thermal', rows: [
        { param: 'Junction-to-Case Resistance',    sym: 'R_θJC',    mtm: [null, th.thermalResistanceJunctionCase, null], unit: '°C/W' },
        { param: 'Junction-to-Ambient Resistance', sym: 'R_θJA',    mtm: [null, th.thermalResistanceJunctionAmbient, null], unit: '°C/W' },
        { param: 'Max Junction Temperature',       sym: 'T_J(max)', mtm: [null, th.junctionTemperatureMax, null], unit: '°C' },
      ]},
      { title: 'Package', rows: [
        { param: 'Assembly', sym: '',  mtm: [null, null, null], str: me.assemblyType ?? me.case ?? '—' },
        { param: 'Length',   sym: 'L', mtm: minTypMax(me.length), unit: 'm' },
        { param: 'Width',    sym: 'W', mtm: minTypMax(me.width), unit: 'm' },
        { param: 'Height',   sym: 'H', mtm: minTypMax(me.height), unit: 'm' },
      ]},
    ],
  }
}

function parseDiode(env) {
  const mi = env?.semiconductor?.diode?.manufacturerInfo ?? {}
  const di = mi.datasheetInfo ?? {}
  const p  = di.part ?? {}
  const el = di.electrical ?? {}
  const th = di.thermal ?? {}
  const me = di.mechanical ?? {}
  return {
    title: mi.reference ?? '—',
    manufacturer: mi.name ?? '—',
    status: mi.status,
    datasheetUrl: mi.datasheetUrl,
    tags: [p.technology, p.subType, p.case].filter(Boolean),
    headline: [
      { sym: 'V_RRM',    label: 'Repetitive Peak Reverse Voltage', val: fmtU(el.reverseVoltage, 'V') },
      { sym: 'I_F(avg)', label: 'Average Forward Current',         val: fmtU(el.forwardCurrent, 'A') },
      { sym: 'V_F',      label: 'Forward Voltage',                 val: fmtU(el.forwardVoltage, 'V') },
    ],
    sections: [
      { title: 'Absolute Maximum Ratings', rows: [
        { param: 'Repetitive Peak Reverse Voltage', sym: 'V_RRM',    mtm: [null, el.reverseVoltage, null], unit: 'V' },
        { param: 'Average Forward Current',         sym: 'I_F(avg)', mtm: [null, el.forwardCurrent, null], unit: 'A' },
        { param: 'Non-Repetitive Surge Current',    sym: 'I_FSM',    mtm: [null, el.surgeCurrent, null], unit: 'A' },
        { param: 'Power Dissipation',               sym: 'P_D',      mtm: [null, el.powerDissipation, null], unit: 'W' },
      ]},
      { title: 'Electrical Characteristics', rows: [
        { param: 'Forward Voltage',         sym: 'V_F',  mtm: [null, el.forwardVoltage, null], unit: 'V', note: el.forwardVoltageAt ? `@ If=${el.forwardVoltageAt}A` : null },
        { param: 'Reverse Leakage Current', sym: 'I_R',  mtm: [null, el.reverseLeakageCurrent, null], unit: 'A' },
        { param: 'Junction Capacitance',    sym: 'C_j',  mtm: [null, el.junctionCapacitance, null], unit: 'F', note: el.junctionCapacitanceVr ? `@ Vr=${el.junctionCapacitanceVr}V` : null },
        { param: 'Reverse Recovery Charge', sym: 'Q_rr', mtm: [null, el.reverseRecoveryCharge, null], unit: 'C' },
      ]},
      { title: 'Thermal', rows: [
        { param: 'Junction-to-Case Resistance',    sym: 'R_θJC',    mtm: [null, th.thermalResistanceJunctionCase, null], unit: '°C/W' },
        { param: 'Junction-to-Ambient Resistance', sym: 'R_θJA',    mtm: [null, th.thermalResistanceJunctionAmbient, null], unit: '°C/W' },
        { param: 'Max Junction Temperature',       sym: 'T_J(max)', mtm: [null, th.junctionTemperatureMax, null], unit: '°C' },
        { param: 'Min Junction Temperature',       sym: 'T_J(min)', mtm: [null, th.junctionTemperatureMin, null], unit: '°C' },
      ]},
      { title: 'Package', rows: [
        { param: 'Assembly', sym: '',  mtm: [null, null, null], str: me.assemblyType ?? '—' },
        { param: 'Length',   sym: 'L', mtm: minTypMax(me.length), unit: 'm' },
        { param: 'Width',    sym: 'W', mtm: minTypMax(me.width), unit: 'm' },
        { param: 'Height',   sym: 'H', mtm: minTypMax(me.height), unit: 'm' },
      ]},
    ],
  }
}

function parseCap(env) {
  const mi = env?.capacitor?.manufacturerInfo ?? {}
  const di = mi.datasheetInfo ?? {}
  const p  = di.part ?? {}
  const el = di.electrical ?? {}
  const th = di.thermal ?? {}
  const me = di.mechanical ?? {}
  const dims = me.dimensions ?? {}
  const shape = me.shape ?? {}
  return {
    title: p.partNumber ?? '—',
    manufacturer: mi.name ?? '—',
    status: mi.status,
    datasheetUrl: mi.datasheetUrl,
    tags: [p.technology, p.case, p.series].filter(Boolean),
    headline: [
      { sym: 'C',   label: 'Capacitance',                  val: fmtU(scalar(el.capacitance), 'F') },
      { sym: 'V_R', label: 'Rated Voltage',                val: fmtU(el.ratedVoltage, 'V') },
      { sym: 'ESR', label: 'Equivalent Series Resistance', val: fmtU(el.esr, 'Ω') },
    ],
    sections: [
      { title: 'Electrical Characteristics', rows: [
        { param: 'Capacitance',           sym: 'C',     mtm: minTypMax(el.capacitance), unit: 'F' },
        { param: 'Rated Voltage (DC)',    sym: 'V_R',   mtm: [null, el.ratedVoltage ?? el.voltageRatedDcMax, null], unit: 'V' },
        { param: 'Dissipation Factor',    sym: 'tan δ', mtm: [null, el.dissipationFactor, null], unit: '', note: el.dissipationFactorFrequency ? `@ ${fmtU(el.dissipationFactorFrequency, 'Hz')}` : null },
        { param: 'ESR',                   sym: 'ESR',   mtm: [null, el.esr, null], unit: 'Ω', note: el.esrFrequency ? `@ ${fmtU(el.esrFrequency, 'Hz')}` : null },
        { param: 'Ripple Current',        sym: 'I_R',   mtm: [null, el.rippleCurrent, null], unit: 'A', note: el.rippleCurrentFrequency ? `@ ${fmtU(el.rippleCurrentFrequency, 'Hz')}, ${el.rippleCurrentTemperature}°C` : null },
        { param: 'Leakage Current',       sym: 'I_L',   mtm: [null, el.leakageCurrent, null], unit: 'A' },
        { param: 'Insulation Resistance', sym: 'R_ins', mtm: [null, el.insulationResistance, null], unit: 'Ω' },
      ]},
      { title: 'Thermal', rows: [
        { param: 'Operating Temperature', sym: 'T_op', mtm: [th.temperature?.minimum ?? null, null, th.temperature?.maximum ?? null], unit: '°C' },
      ]},
      { title: 'Package', rows: [
        { param: 'Assembly', sym: '',  mtm: [null, null, null], str: shape.assembly ?? '—' },
        { param: 'Shape',    sym: '',  mtm: [null, null, null], str: shape.shapeType ?? '—' },
        { param: 'Diameter', sym: 'ø', mtm: minTypMax(dims.diameter ?? me.diameter), unit: 'm' },
        { param: 'Length',   sym: 'L', mtm: minTypMax(dims.length ?? me.length), unit: 'm' },
        { param: 'Width',    sym: 'W', mtm: minTypMax(dims.width ?? me.width), unit: 'm' },
        { param: 'Height',   sym: 'H', mtm: minTypMax(dims.height ?? me.height), unit: 'm' },
      ]},
    ],
  }
}

function parseRes(env) {
  const mi = env?.resistor?.manufacturerInfo ?? {}
  const di = mi.datasheetInfo ?? {}
  const p  = di.part ?? {}
  const el = di.electrical ?? {}
  const th = di.thermal ?? {}
  const me = di.mechanical ?? {}
  return {
    title: p.partNumber ?? '—',
    manufacturer: mi.name ?? '—',
    status: mi.status,
    datasheetUrl: mi.datasheetUrl,
    tags: [p.technology, p.case, p.series].filter(Boolean),
    headline: [
      { sym: 'R',   label: 'Resistance',   val: fmtU(scalar(el.resistance), 'Ω') },
      { sym: 'Tol', label: 'Tolerance',    val: el.tolerance != null ? `${+(el.tolerance * 100).toPrecision(3)}%` : '—' },
      { sym: 'P',   label: 'Power Rating', val: fmtU(el.powerRating, 'W') },
    ],
    sections: [
      { title: 'Electrical Characteristics', rows: [
        { param: 'Resistance',              sym: 'R',     mtm: minTypMax(el.resistance), unit: 'Ω' },
        { param: 'Tolerance',               sym: 'Tol',   mtm: [null, null, null], str: el.tolerance != null ? `±${+(el.tolerance * 100).toPrecision(3)}%` : '—' },
        { param: 'Temperature Coefficient', sym: 'TCR',   mtm: [null, el.temperatureCoefficient, null], unit: 'ppm/°C' },
        { param: 'Power Rating',            sym: 'P',     mtm: [null, el.powerRating, null], unit: 'W', note: el.powerRatingTemperature ? `@ ${el.powerRatingTemperature}°C` : null },
        { param: 'Max Voltage',             sym: 'V_max', mtm: [null, el.maxVoltage, null], unit: 'V' },
      ]},
      { title: 'Thermal', rows: [
        { param: 'Operating Temperature', sym: 'T_op', mtm: [th.operatingTemperature?.minimum ?? null, null, th.operatingTemperature?.maximum ?? null], unit: '°C' },
      ]},
      { title: 'Package', rows: [
        { param: 'Assembly', sym: '',  mtm: [null, null, null], str: me.assemblyType ?? '—' },
        { param: 'Shape',    sym: '',  mtm: [null, null, null], str: me.shapeType ?? '—' },
        { param: 'Length',   sym: 'L', mtm: minTypMax(me.length), unit: 'm' },
        { param: 'Width',    sym: 'W', mtm: minTypMax(me.width), unit: 'm' },
        { param: 'Height',   sym: 'H', mtm: minTypMax(me.height), unit: 'm' },
      ]},
    ],
  }
}

function parseMag(env) {
  const mi = env?.magnetic?.manufacturerInfo ?? {}
  const di = mi.datasheetInfo ?? {}
  const p  = di.part ?? {}
  const elRaw = di.electrical ?? []
  const el = Array.isArray(elRaw) ? (elRaw[0] ?? {}) : elRaw
  const th = di.thermal ?? {}
  const me = di.mechanical ?? {}
  const ratedI = el.ratedCurrents?.[0] ?? null
  return {
    title: mi.reference ?? '—',
    manufacturer: mi.name ?? '—',
    status: mi.status,
    datasheetUrl: mi.datasheetUrl,
    tags: [mi.family, el.subtype ?? 'inductor', p.caseCode, p.material, p.shielded ? 'shielded' : null].filter(Boolean),
    headline: [
      { sym: 'L',     label: 'Inductance',                val: fmtU(scalar(el.inductance), 'H') },
      { sym: 'I_sat', label: 'Saturation Current (peak)', val: fmtU(el.saturationCurrentPeak, 'A') },
      { sym: 'DCR',   label: 'DC Resistance',             val: fmtU(scalar(el.dcResistance), 'Ω') },
    ],
    sections: [
      { title: 'Electrical Characteristics', rows: [
        { param: 'Inductance',                sym: 'L',       mtm: minTypMax(el.inductance), unit: 'H' },
        { param: 'DC Resistance',             sym: 'DCR',     mtm: minTypMax(el.dcResistance), unit: 'Ω' },
        { param: 'Saturation Current (peak)', sym: 'I_sat',   mtm: [null, el.saturationCurrentPeak, null], unit: 'A' },
        { param: 'Rated Current',             sym: 'I_rated', mtm: [null, ratedI, null], unit: 'A' },
        { param: 'Self-Resonant Frequency',   sym: 'SRF',     mtm: [null, el.selfResonantFrequency, null], unit: 'Hz' },
      ]},
      { title: 'Thermal', rows: [
        { param: 'Operating Temperature', sym: 'T_op', mtm: [th.operatingTemperature?.minimum ?? null, null, th.operatingTemperature?.maximum ?? null], unit: '°C' },
      ]},
      { title: 'Package', rows: [
        { param: 'Case Code',     sym: '',  mtm: [null, null, null], str: p.caseCode ?? '—' },
        { param: 'Material',      sym: '',  mtm: [null, null, null], str: p.material ?? '—' },
        { param: 'Winding Style', sym: '',  mtm: [null, null, null], str: p.windingStyle ?? '—' },
        { param: 'Length',        sym: 'L', mtm: minTypMax(me.length), unit: 'm' },
        { param: 'Width',         sym: 'W', mtm: minTypMax(me.width), unit: 'm' },
        { param: 'Height',        sym: 'H', mtm: minTypMax(me.height), unit: 'm' },
      ]},
    ],
  }
}

function parseConnector(env) {
  const mi = env?.connector?.manufacturerInfo ?? {}
  const di = mi.datasheetInfo ?? {}
  const p  = di.part ?? {}
  const el = di.electrical ?? {}
  const me = di.mechanical ?? {}
  const fam = di.familyDetails ?? {}
  const ev = di.environmental ?? {}
  // Optional string rows: str stays undefined when the field is absent, so the
  // row is skipped instead of rendering a dash.
  const opt = (v) => (v != null ? String(v) : undefined)
  return {
    title: mi.reference ?? p.partNumber ?? '—',
    manufacturer: mi.name ?? '—',
    status: mi.status,
    datasheetUrl: mi.datasheetUrl,
    description: p.description ?? mi.description,
    tags: [words(fam.family), p.series, p.matingPolarity, words(me.orientation), words(me.mountingStyle)].filter(Boolean),
    headline: [
      { sym: 'V_R',  label: 'Rated Voltage',             val: fmtU(el.ratedVoltage, 'V') },
      { sym: 'I/ct', label: 'Rated Current per Contact', val: fmtU(el.ratedCurrentPerContact, 'A') },
      { sym: 'Pos',  label: 'Positions',                 val: me.positions != null ? String(me.positions) : '—' },
    ],
    sections: [
      { title: 'Electrical Characteristics', rows: [
        { param: 'Rated Voltage',                  sym: 'V_R',   mtm: [null, el.ratedVoltage, null], unit: 'V' },
        { param: 'Rated Current per Contact',      sym: 'I/ct',  mtm: [null, el.ratedCurrentPerContact, null], unit: 'A', note: el.ratedCurrentReferenceTemperature != null ? `@ ${el.ratedCurrentReferenceTemperature}°C` : null },
        { param: 'Contact Resistance',             sym: 'R_ct',  mtm: [null, el.contactResistance, null], unit: 'Ω' },
        { param: 'Insulation Resistance',          sym: 'R_ins', mtm: [null, el.insulationResistance, null], unit: 'Ω' },
        { param: 'Dielectric Withstanding Voltage', sym: 'V_DW', mtm: [null, el.dielectricWithstandingVoltage, null], unit: 'V' },
        { param: 'Clearance',                      sym: '',      mtm: [null, el.clearance, null], unit: 'm' },
        { param: 'Creepage',                       sym: '',      mtm: [null, el.creepage, null], unit: 'm' },
      ]},
      { title: 'Contacts & Mounting', rows: [
        { param: 'Positions',       sym: '', mtm: [null, null, null], str: me.positions != null ? String(me.positions) : '—' },
        { param: 'Rows',            sym: '', mtm: [null, null, null], str: opt(me.rows) },
        { param: 'Pitch',           sym: '', mtm: [null, me.pitch, null], unit: 'm' },
        { param: 'Row Pitch',       sym: '', mtm: [null, me.rowPitch, null], unit: 'm' },
        { param: 'Orientation',     sym: '', mtm: [null, null, null], str: opt(words(me.orientation)) },
        { param: 'Mounting',        sym: '', mtm: [null, null, null], str: opt(words(me.mountingStyle)) },
        { param: 'Mating Polarity', sym: '', mtm: [null, null, null], str: opt(p.matingPolarity) },
      ]},
      { title: 'Environmental', rows: [
        { param: 'Operating Temperature', sym: 'T_op', mtm: [ev.operatingTemperature?.minimum ?? null, null, ev.operatingTemperature?.maximum ?? null], unit: '°C' },
        { param: 'IP Rating',      sym: '', mtm: [null, null, null], str: opt(ev.ipRating) },
        { param: 'Solder Process', sym: '', mtm: [null, null, null], str: opt(words(ev.solderProcess)) },
      ]},
      { title: 'Package', rows: [
        { param: 'Length',   sym: 'L', mtm: minTypMax(me.length), unit: 'm' },
        { param: 'Width',    sym: 'W', mtm: minTypMax(me.width), unit: 'm' },
        { param: 'Height',   sym: 'H', mtm: minTypMax(me.height), unit: 'm' },
        { param: 'Diameter', sym: 'ø', mtm: minTypMax(me.diameter), unit: 'm' },
      ]},
    ],
  }
}

const PARSERS = {
  mosfets: parseMosfet,
  diodes: parseDiode,
  capacitors: parseCap,
  resistors: parseRes,
  magnetics: parseMag,
  connectors: parseConnector,
}

// Copy text to the clipboard; falls back to execCommand for non-secure
// contexts (plain-http LAN hosts) where navigator.clipboard is unavailable.
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
  } catch (e) {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    ta.remove()
  }
}

// A section is worth rendering only if at least one row has content.
export function sectionVisible(sec) {
  return sec.rows.some((r) => r.str !== undefined || hasAny(r.mtm, r.unit))
}

// env is the raw PEAS envelope from /catalog/<cat>/<mpn>/detail (.data field).
// Returns null when the category has no datasheet view.
export function parseSheet(category, env) {
  const parse = PARSERS[category]
  return parse ? parse(env) : null
}
