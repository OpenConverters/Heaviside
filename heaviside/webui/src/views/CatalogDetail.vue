<script setup>
import { ref, computed, onMounted } from 'vue'
import Button from 'primevue/button'
import Tag from 'primevue/tag'

const props = defineProps({ mpn: String, category: String })
const emit = defineEmits(['close'])

const loading = ref(false)
const error = ref(null)
const data = ref(null)

onMounted(async () => {
  if (!props.mpn || !props.category) return
  loading.value = true
  try {
    const r = await fetch(`/catalog/${props.category}/${encodeURIComponent(props.mpn)}/detail`)
    if (!r.ok) throw new Error(`${r.status}`)
    data.value = await r.json()
  } catch (e) { error.value = String(e) }
  finally { loading.value = false }
})

// ── Value formatters ──────────────────────────────────────────────────────────
function fmtVal(v, digits = 3) {
  if (v == null) return null
  const abs = Math.abs(v)
  if (abs === 0) return '0'
  if (abs >= 1e9) return `${+(v / 1e9).toPrecision(digits)} G`
  if (abs >= 1e6) return `${+(v / 1e6).toPrecision(digits)} M`
  if (abs >= 1e3) return `${+(v / 1e3).toPrecision(digits)} k`
  if (abs >= 1)   return `${+v.toPrecision(digits)} `
  if (abs >= 1e-3) return `${+(v * 1e3).toPrecision(digits)} m`
  if (abs >= 1e-6) return `${+(v * 1e6).toPrecision(digits)} µ`
  if (abs >= 1e-9) return `${+(v * 1e9).toPrecision(digits)} n`
  if (abs >= 1e-12) return `${+(v * 1e12).toPrecision(digits)} p`
  return `${+v.toPrecision(digits)} `
}
function fmtU(v, unit) { const s = fmtVal(v); return s != null ? `${s}${unit}` : '—' }
function scalar(v) {
  if (v == null) return null
  if (typeof v === 'number') return v
  return v.nominal ?? v.typical ?? v.maximum ?? v.minimum ?? null
}
function minTypMax(v) {
  if (v == null) return [null, null, null]
  if (typeof v === 'number') return [null, v, null]
  return [v.minimum ?? null, v.nominal ?? v.typical ?? null, v.maximum ?? null]
}
function fmtMTM([mn, ty, mx], unit) {
  if (!unit) return { min: mn ?? '—', typ: ty ?? '—', max: mx ?? '—' }
  return {
    min: mn != null ? fmtU(mn, unit) : '—',
    typ: ty != null ? fmtU(ty, unit) : '—',
    max: mx != null ? fmtU(mx, unit) : '—',
  }
}
function hasAny(mtm, unit) {
  const f = fmtMTM(mtm, unit)
  return f.min !== '—' || f.typ !== '—' || f.max !== '—'
}

// ── Per-category parsers (same logic as ComponentDatasheet.vue) ───────────────
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
      { sym: 'V_DS',     label: 'Drain-Source Voltage',    val: fmtU(el.drainSourceVoltage, 'V') },
      { sym: 'R_DS(on)', label: 'On Resistance',           val: fmtU(el.onResistance, 'Ω') },
      { sym: 'I_D',      label: 'Continuous Drain Current', val: fmtU(el.continuousDrainCurrent, 'A') },
    ],
    sections: [
      { title: 'Absolute Maximum Ratings', rows: [
        { param: 'Drain-Source Voltage',    sym: 'V_DS',      mtm: [null, el.drainSourceVoltage, null], unit: 'V' },
        { param: 'Gate-Source Voltage',     sym: 'V_GS',      mtm: [null, el.gateSourceVoltageMax, null], unit: 'V' },
        { param: 'Continuous Drain Current', sym: 'I_D',      mtm: [null, el.continuousDrainCurrent, null], unit: 'A' },
        { param: 'Pulsed Drain Current',    sym: 'I_D(pulse)', mtm: [null, el.pulsedDrainCurrent, null], unit: 'A' },
        { param: 'Power Dissipation',       sym: 'P_D',       mtm: [null, el.powerDissipation, null], unit: 'W' },
      ]},
      { title: 'Static Characteristics', rows: [
        { param: 'On Resistance',           sym: 'R_DS(on)', mtm: minTypMax(el.onResistance), unit: 'Ω', note: el.onResistanceVgs ? `@ Vgs=${el.onResistanceVgs}V, Id=${el.onResistanceId}A` : null },
        { param: 'Gate Threshold Voltage',  sym: 'V_GS(th)', mtm: minTypMax(el.gateThresholdVoltage), unit: 'V' },
        { param: 'Body Diode Forward Voltage', sym: 'V_SD', mtm: [null, el.bodyDiodeForwardVoltage, null], unit: 'V' },
      ]},
      { title: 'Dynamic Characteristics', rows: [
        { param: 'Input Capacitance',             sym: 'C_iss', mtm: [null, scalar(el.inputCapacitance), null], unit: 'F' },
        { param: 'Output Capacitance',            sym: 'C_oss', mtm: [null, scalar(el.outputCapacitance), null], unit: 'F' },
        { param: 'Reverse Transfer Capacitance',  sym: 'C_rss', mtm: [null, scalar(el.reverseTransferCapacitance), null], unit: 'F' },
        { param: 'Total Gate Charge',             sym: 'Q_g',   mtm: [null, scalar(el.totalGateCharge), null], unit: 'C' },
        { param: 'Gate-Source Charge',            sym: 'Q_gs',  mtm: [null, scalar(el.gateSourceCharge), null], unit: 'C' },
        { param: 'Gate-Drain Charge',             sym: 'Q_gd',  mtm: [null, scalar(el.gateDrainCharge), null], unit: 'C' },
        { param: 'Reverse Recovery Charge',       sym: 'Q_rr',  mtm: [null, scalar(el.reverseRecoveryCharge), null], unit: 'C' },
        { param: 'Figure of Merit',               sym: 'FOM',   mtm: [null, scalar(el.figureOfMerit), null], unit: 'Ω·C' },
      ]},
      { title: 'Thermal', rows: [
        { param: 'Junction-to-Case Resistance',   sym: 'R_θJC',    mtm: [null, th.thermalResistanceJunctionCase, null], unit: '°C/W' },
        { param: 'Junction-to-Ambient Resistance', sym: 'R_θJA',   mtm: [null, th.thermalResistanceJunctionAmbient, null], unit: '°C/W' },
        { param: 'Max Junction Temperature',      sym: 'T_J(max)', mtm: [null, th.junctionTemperatureMax, null], unit: '°C' },
      ]},
      { title: 'Package', rows: [
        { param: 'Assembly', sym: '', mtm: [null, null, null], str: me.assemblyType ?? me.case ?? '—' },
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
      { sym: 'V_RRM',   label: 'Repetitive Peak Reverse Voltage', val: fmtU(el.reverseVoltage, 'V') },
      { sym: 'I_F(avg)', label: 'Average Forward Current',        val: fmtU(el.forwardCurrent, 'A') },
      { sym: 'V_F',      label: 'Forward Voltage',                val: fmtU(el.forwardVoltage, 'V') },
    ],
    sections: [
      { title: 'Absolute Maximum Ratings', rows: [
        { param: 'Repetitive Peak Reverse Voltage', sym: 'V_RRM',  mtm: [null, el.reverseVoltage, null], unit: 'V' },
        { param: 'Average Forward Current',         sym: 'I_F(avg)', mtm: [null, el.forwardCurrent, null], unit: 'A' },
        { param: 'Non-Repetitive Surge Current',    sym: 'I_FSM',  mtm: [null, el.surgeCurrent, null], unit: 'A' },
        { param: 'Power Dissipation',               sym: 'P_D',    mtm: [null, el.powerDissipation, null], unit: 'W' },
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
        { param: 'Assembly', sym: '', mtm: [null, null, null], str: me.assemblyType ?? '—' },
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
      { sym: 'C',   label: 'Capacitance',               val: fmtU(scalar(el.capacitance), 'F') },
      { sym: 'V_R', label: 'Rated Voltage',              val: fmtU(el.ratedVoltage, 'V') },
      { sym: 'ESR', label: 'Equivalent Series Resistance', val: fmtU(el.esr, 'Ω') },
    ],
    sections: [
      { title: 'Electrical Characteristics', rows: [
        { param: 'Capacitance',         sym: 'C',     mtm: minTypMax(el.capacitance), unit: 'F' },
        { param: 'Rated Voltage (DC)',   sym: 'V_R',   mtm: [null, el.ratedVoltage ?? el.voltageRatedDcMax, null], unit: 'V' },
        { param: 'Dissipation Factor',  sym: 'tan δ', mtm: [null, el.dissipationFactor, null], unit: '', note: el.dissipationFactorFrequency ? `@ ${fmtU(el.dissipationFactorFrequency, 'Hz')}` : null },
        { param: 'ESR',                 sym: 'ESR',   mtm: [null, el.esr, null], unit: 'Ω', note: el.esrFrequency ? `@ ${fmtU(el.esrFrequency, 'Hz')}` : null },
        { param: 'Ripple Current',      sym: 'I_R',   mtm: [null, el.rippleCurrent, null], unit: 'A', note: el.rippleCurrentFrequency ? `@ ${fmtU(el.rippleCurrentFrequency, 'Hz')}, ${el.rippleCurrentTemperature}°C` : null },
        { param: 'Leakage Current',     sym: 'I_L',   mtm: [null, el.leakageCurrent, null], unit: 'A' },
        { param: 'Insulation Resistance', sym: 'R_ins', mtm: [null, el.insulationResistance, null], unit: 'Ω' },
      ]},
      { title: 'Thermal', rows: [
        { param: 'Operating Temperature', sym: 'T_op', mtm: [th.temperature?.minimum ?? null, null, th.temperature?.maximum ?? null], unit: '°C' },
      ]},
      { title: 'Package', rows: [
        { param: 'Assembly', sym: '', mtm: [null, null, null], str: shape.assembly ?? '—' },
        { param: 'Shape',    sym: '', mtm: [null, null, null], str: shape.shapeType ?? '—' },
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
      { sym: 'R',   label: 'Resistance',    val: fmtU(scalar(el.resistance), 'Ω') },
      { sym: 'Tol', label: 'Tolerance',     val: el.tolerance != null ? `${+(el.tolerance * 100).toPrecision(3)}%` : '—' },
      { sym: 'P',   label: 'Power Rating',  val: fmtU(el.powerRating, 'W') },
    ],
    sections: [
      { title: 'Electrical Characteristics', rows: [
        { param: 'Resistance',            sym: 'R',    mtm: minTypMax(el.resistance), unit: 'Ω' },
        { param: 'Tolerance',             sym: 'Tol',  mtm: [null, null, null], str: el.tolerance != null ? `±${+(el.tolerance * 100).toPrecision(3)}%` : '—' },
        { param: 'Temperature Coefficient', sym: 'TCR', mtm: [null, el.temperatureCoefficient, null], unit: 'ppm/°C' },
        { param: 'Power Rating',          sym: 'P',    mtm: [null, el.powerRating, null], unit: 'W', note: el.powerRatingTemperature ? `@ ${el.powerRatingTemperature}°C` : null },
        { param: 'Max Voltage',           sym: 'V_max', mtm: [null, el.maxVoltage, null], unit: 'V' },
      ]},
      { title: 'Thermal', rows: [
        { param: 'Operating Temperature', sym: 'T_op', mtm: [th.operatingTemperature?.minimum ?? null, null, th.operatingTemperature?.maximum ?? null], unit: '°C' },
      ]},
      { title: 'Package', rows: [
        { param: 'Assembly', sym: '', mtm: [null, null, null], str: me.assemblyType ?? '—' },
        { param: 'Shape',    sym: '', mtm: [null, null, null], str: me.shapeType ?? '—' },
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
      { sym: 'L',     label: 'Inductance',              val: fmtU(scalar(el.inductance), 'H') },
      { sym: 'I_sat', label: 'Saturation Current (peak)', val: fmtU(el.saturationCurrentPeak, 'A') },
      { sym: 'DCR',   label: 'DC Resistance',           val: fmtU(scalar(el.dcResistance), 'Ω') },
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
        { param: 'Case Code',     sym: '', mtm: [null, null, null], str: p.caseCode ?? '—' },
        { param: 'Material',      sym: '', mtm: [null, null, null], str: p.material ?? '—' },
        { param: 'Winding Style', sym: '', mtm: [null, null, null], str: p.windingStyle ?? '—' },
        { param: 'Length',        sym: 'L', mtm: minTypMax(me.length), unit: 'm' },
        { param: 'Width',         sym: 'W', mtm: minTypMax(me.width), unit: 'm' },
        { param: 'Height',        sym: 'H', mtm: minTypMax(me.height), unit: 'm' },
      ]},
    ],
  }
}

const sheet = computed(() => {
  if (!data.value) return null
  const env = data.value.data
  const cat = data.value.category
  if (cat === 'mosfets')    return parseMosfet(env)
  if (cat === 'diodes')     return parseDiode(env)
  if (cat === 'capacitors') return parseCap(env)
  if (cat === 'resistors')  return parseRes(env)
  if (cat === 'magnetics')  return parseMag(env)
  return null
})
</script>

<template>
  <div class="panel">
    <!-- Back nav -->
    <div class="detail-nav">
      <Button icon="pi pi-arrow-left" label="Back to catalog" text size="small"
              severity="secondary" @click="emit('close')" />
      <span v-if="sheet" class="detail-breadcrumb mono">
        {{ props.category }} / {{ sheet.title }}
      </span>
    </div>

    <div v-if="loading" class="detail-state stage-line">Loading…</div>
    <div v-else-if="error" class="detail-state" style="color:var(--fault)">{{ error }}</div>

    <template v-else-if="sheet">
      <!-- ── Header ── -->
      <div class="detail-header">
        <div class="detail-mfr mono">{{ sheet.manufacturer }}</div>
        <div class="detail-mpn">{{ sheet.title }}</div>
        <div class="detail-tags">
          <Tag v-for="t in sheet.tags" :key="t" :value="t" severity="secondary" class="detail-tag" />
          <Tag v-if="sheet.status" :value="sheet.status"
               :severity="sheet.status === 'production' ? 'success' : 'secondary'" class="detail-tag" />
        </div>
        <a v-if="sheet.datasheetUrl" :href="sheet.datasheetUrl" target="_blank"
           rel="noopener" class="detail-pdf mono">
          ↗ Manufacturer Datasheet
        </a>
      </div>

      <!-- ── Headline KV strip ── -->
      <div class="detail-headline">
        <div v-for="h in sheet.headline" :key="h.sym" class="detail-kv">
          <span class="detail-sym mono">{{ h.sym }}</span>
          <span class="detail-kv-val mono">{{ h.val }}</span>
          <span class="detail-kv-label">{{ h.label }}</span>
        </div>
      </div>

      <!-- ── Parameter sections ── -->
      <div class="detail-sections">
        <div v-for="sec in sheet.sections" :key="sec.title" class="detail-section">
          <div class="detail-sec-title">{{ sec.title }}</div>
          <table class="detail-table">
            <thead>
              <tr>
                <th class="col-param">Parameter</th>
                <th class="col-sym">Symbol</th>
                <th class="col-val num">Min</th>
                <th class="col-val num">Typ</th>
                <th class="col-val num">Max</th>
                <th class="col-note">Conditions</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="row in sec.rows" :key="row.param">
                <tr v-if="row.str !== undefined || hasAny(row.mtm, row.unit)">
                  <td class="col-param">{{ row.param }}</td>
                  <td class="col-sym mono">{{ row.sym }}</td>
                  <template v-if="row.str !== undefined">
                    <td class="col-val num" colspan="3">{{ row.str }}</td>
                  </template>
                  <template v-else>
                    <td v-for="v in [fmtMTM(row.mtm, row.unit).min, fmtMTM(row.mtm, row.unit).typ, fmtMTM(row.mtm, row.unit).max]"
                        :key="v" class="col-val num mono">{{ v }}</td>
                  </template>
                  <td class="col-note muted">{{ row.note ?? '' }}</td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.detail-nav {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1.2rem;
}
.detail-breadcrumb {
  font-size: .68rem;
  color: var(--p-surface-500);
  letter-spacing: .06em;
  text-transform: uppercase;
}
.detail-state {
  padding: 2rem 0;
  font-size: .85rem;
}

/* Header */
.detail-header { margin-bottom: 1.4rem; }
.detail-mfr {
  font-size: .72rem;
  color: var(--p-surface-400);
  letter-spacing: .06em;
  text-transform: uppercase;
  margin-bottom: .2rem;
}
.detail-mpn {
  font-size: 1.5rem;
  font-weight: 700;
  letter-spacing: .02em;
  color: var(--p-surface-50);
  line-height: 1.2;
  margin-bottom: .5rem;
}
.detail-tags { display: flex; flex-wrap: wrap; gap: .3rem; margin-bottom: .6rem; }
.detail-tag { font-size: .65rem !important; }
.detail-pdf { font-size: .72rem; color: var(--ch1); text-decoration: none; }
.detail-pdf:hover { text-decoration: underline; }

/* Headline */
.detail-headline {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: .6rem;
  background: var(--p-surface-800);
  border: 1px solid var(--p-surface-700);
  border-radius: 6px;
  padding: .9rem 1.2rem;
  margin-bottom: 1.8rem;
}
.detail-kv { display: flex; flex-direction: column; gap: .1rem; }
.detail-sym { font-size: .68rem; color: var(--p-surface-400); }
.detail-kv-val { font-size: 1.1rem; font-weight: 700; color: var(--ch1); line-height: 1.1; }
.detail-kv-label { font-size: .62rem; color: var(--p-surface-500); }

/* Sections — two-column on wide screens */
.detail-sections {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
  gap: 1.4rem 2rem;
  align-items: start;
}
.detail-section {}
.detail-sec-title {
  font-size: .65rem;
  font-weight: 700;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: var(--ch1);
  border-bottom: 1px solid var(--ch1-deep, #129e8b);
  padding-bottom: .3rem;
  margin-bottom: .5rem;
}

.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: .73rem;
  line-height: 1.4;
}
.detail-table th {
  text-align: left;
  font-size: .6rem;
  font-weight: 600;
  letter-spacing: .07em;
  text-transform: uppercase;
  color: var(--p-surface-500);
  border-bottom: 1px solid var(--p-surface-700);
  padding: .2rem .4rem .3rem;
}
.detail-table td {
  padding: .25rem .4rem;
  border-bottom: 1px solid var(--p-surface-800);
  color: var(--p-surface-200);
  vertical-align: middle;
}
.detail-table tbody tr:hover td { background: var(--p-surface-800); }
.col-param { min-width: 160px; }
.col-sym   { width: 70px; color: var(--p-surface-400); font-size: .68rem; }
.col-val   { width: 72px; }
.col-val.num { text-align: right; font-family: var(--mono); }
.col-note  { color: var(--p-surface-500); font-size: .65rem; }
</style>
