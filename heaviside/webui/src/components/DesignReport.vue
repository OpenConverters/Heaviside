<script setup>
import { ref, computed } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import WaveformChart from './WaveformChart.vue'
import { useDatasheet, inferCategory } from '../composables/useDatasheet.js'

const props = defineProps({
  result: { type: Object, required: true },
  pdfUrl: { type: String, default: '' },
})

const { openDatasheet } = useDatasheet()

const waveforms = computed(() => props.result?.waveforms || [])
const ops = computed(() => props.result?.operating_points || [])
const bom = computed(() => props.result?.bom || [])

// Active operating point for the waveform pane.
const opSel = ref(0)
const activeWf = computed(() => waveforms.value[opSel.value] || null)
const activeOp = computed(() => {
  const wf = activeWf.value
  if (!wf) return null
  return ops.value.find((o) => o.op_index === wf.op_index) || ops.value[opSel.value] || null
})

const verdictSeverity = computed(() =>
  props.result?.verdict === 'pass' ? 'success'
    : props.result?.verdict === 'fail' ? 'danger' : 'warn')

const showRaw = ref(false)
const expanded = ref({})

// SI-compact value formatter for the BOM port-stress columns.
function fmt(v, unit) {
  if (v == null || !isFinite(v)) return '—'
  const a = Math.abs(v)
  let s = v, p = ''
  if (a >= 1e9) { s = v / 1e9; p = 'G' }
  else if (a >= 1e6) { s = v / 1e6; p = 'M' }
  else if (a >= 1e3) { s = v / 1e3; p = 'k' }
  else if (a >= 1) { s = v; p = '' }
  else if (a >= 1e-3) { s = v * 1e3; p = 'm' }
  else if (a >= 1e-6) { s = v * 1e6; p = 'µ' }
  else if (a >= 1e-9) { s = v * 1e9; p = 'n' }
  else if (a !== 0) { s = v * 1e12; p = 'p' }
  return `${+s.toPrecision(3)} ${p}${unit}`
}
const fmtHz = (v) => fmt(v, 'Hz')

function railSummary(op) {
  if (!op) return ''
  const vs = op.output_voltages || []
  const is = op.output_currents || []
  return vs.map((v, i) => `${+(+v).toPrecision(3)} V @ ${+(+(is[i] ?? 0)).toPrecision(3)} A`).join(' · ')
}

// Stress ÷ rating → headroom; <1.0 means over-stressed (red), tight if <1.3.
function margin(stress, rated) {
  if (stress == null || rated == null || !(stress > 0) || !(rated > 0)) return null
  return rated / stress
}
function marginClass(m) {
  if (m == null) return ''
  if (m < 1.0) return 'm-bad'
  if (m < 1.3) return 'm-warn'
  return 'm-good'
}

function dsOpen(row) {
  const cat = inferCategory(row.category)
  if (row.mpn && cat) openDatasheet(row.mpn, cat)
}
const clickable = (row) => !!(row.mpn && inferCategory(row.category))

const isPassive = (row) =>
  ['capacitor', 'inductor', 'magnetic', 'magnetics', 'resistor'].includes(
    (row.category || '').toLowerCase())
</script>

<template>
  <div class="dr">
    <!-- ── Summary header ─────────────────────────────────────────── -->
    <div class="dr-head">
      <div class="dr-topo">
        {{ (result.topology || 'converter').replace(/_/g, ' ') }}
        <Tag :severity="verdictSeverity" :value="(result.verdict || 'n/a').toUpperCase()" />
      </div>
      <div class="dr-chips">
        <span v-if="result.fsw_hz" class="chip mono">fsw {{ fmtHz(result.fsw_hz) }}</span>
        <span class="chip mono">{{ bom.length }} parts</span>
        <span class="chip mono">{{ waveforms.length }} operating points</span>
        <a v-if="pdfUrl" :href="pdfUrl" target="_blank" class="chip chip-link">
          <i class="pi pi-download" /> PDF
        </a>
      </div>
    </div>

    <!-- ── Waveforms ──────────────────────────────────────────────── -->
    <section class="dr-sec">
      <h3>Simulation waveforms <span class="sub">— inductor / primary winding, per operating point</span></h3>
      <div v-if="waveforms.length" class="op-tabs">
        <button v-for="(wf, i) in waveforms" :key="i" class="op-tab" :class="{ sel: opSel === i }"
                @click="opSel = i">{{ wf.label || ('OP' + (wf.op_index ?? i)) }}</button>
      </div>
      <div v-if="activeOp" class="op-meta mono">
        <span v-if="activeOp.vin_nominal != null">Vin {{ +(+activeOp.vin_nominal).toPrecision(3) }} V</span>
        <span>{{ railSummary(activeOp) }}</span>
        <span v-if="activeOp.fsw_hz">fsw {{ fmtHz(activeOp.fsw_hz) }}</span>
        <span v-if="activeOp.ambient_c != null">Tamb {{ activeOp.ambient_c }} °C</span>
      </div>
      <WaveformChart v-if="activeWf" :wf="activeWf" :height="220" />
      <div v-else class="dr-empty">
        No simulated waveforms were captured for this design.
      </div>
    </section>

    <!-- ── Bill of materials ──────────────────────────────────────── -->
    <section class="dr-sec">
      <h3>Bill of materials
        <span class="sub">— click an MPN for its datasheet; passives show their port voltage &amp; current</span>
      </h3>
      <DataTable :value="bom" size="small" stripedRows removableSort dataKey="ref"
                 v-model:expandedRows="expanded"
                 @rowClick="(e) => (expanded[e.data.ref] = expanded[e.data.ref] ? undefined : e.data)"
                 rowHover>
        <Column expander style="width:2.5rem" />
        <Column field="ref" header="Ref" sortable bodyClass="col-mpn" />
        <Column field="category" header="Type" sortable>
          <template #body="{ data }">
            <span class="type-cell">{{ data.category || '—' }}<i v-if="isPassive(data)" class="passive-dot" /></span>
          </template>
        </Column>
        <Column field="mpn" header="MPN" sortable bodyClass="col-mpn">
          <template #body="{ data }">
            <span class="mpn-chip" :class="{ 'mpn-clickable': clickable(data) }"
                  @click.stop="dsOpen(data)">{{ data.mpn || '—' }}</span>
          </template>
        </Column>
        <Column field="manufacturer" header="Manufacturer" sortable />
        <Column header="Port V">
          <template #body="{ data }">
            <span :class="marginClass(margin(data.port_voltage, data.rated_voltage))">
              {{ fmt(data.port_voltage, 'V') }}</span>
            <span v-if="data.rated_voltage" class="rated">/ {{ fmt(data.rated_voltage, 'V') }}</span>
          </template>
        </Column>
        <Column header="Port I">
          <template #body="{ data }">
            <span :class="marginClass(margin(data.port_current, data.rated_current))">
              {{ fmt(data.port_current, 'A') }}</span>
            <span v-if="data.rated_current" class="rated">/ {{ fmt(data.rated_current, 'A') }}</span>
          </template>
        </Column>
        <template #expansion="{ data }">
          <div class="bom-detail">
            <div class="bom-grid">
              <div><span class="lbl">Port voltage (operating)</span>{{ fmt(data.port_voltage, 'V') }}</div>
              <div><span class="lbl">Rated voltage</span>{{ fmt(data.rated_voltage, 'V') }}</div>
              <div><span class="lbl">Port current (operating)</span>{{ fmt(data.port_current, 'A') }}</div>
              <div><span class="lbl">Rated current</span>{{ fmt(data.rated_current, 'A') }}</div>
            </div>
            <Button v-if="clickable(data)" icon="pi pi-file" :label="'Datasheet · ' + data.mpn"
                    size="small" text severity="secondary" @click="dsOpen(data)" />
            <span v-else class="muted">No catalog datasheet for this category.</span>
          </div>
        </template>
        <template #empty><span class="muted">No BOM produced for this design.</span></template>
      </DataTable>
    </section>

    <!-- ── Full rendered report (fallback / detail) ───────────────── -->
    <section v-if="result.html" class="dr-sec">
      <div class="raw-toggle" @click="showRaw = !showRaw">
        <i :class="showRaw ? 'pi pi-chevron-down' : 'pi pi-chevron-right'" />
        Full design report <span class="sub">(realism checks, margins, reviewer panel)</span>
      </div>
      <div v-show="showRaw" class="report-html" v-html="result.html"></div>
    </section>
  </div>
</template>

<style scoped>
.dr { display: flex; flex-direction: column; gap: 1.3rem; }
.dr-head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.dr-topo { font-size: 1.05rem; font-weight: 600; text-transform: capitalize;
  display: inline-flex; align-items: center; gap: .6rem; }
.dr-chips { display: flex; gap: .4rem; align-items: center; flex-wrap: wrap; }
.chip { font-size: .7rem; padding: .2rem .55rem; border-radius: 6px; background: var(--p-surface-800);
  color: var(--p-surface-300); }
.chip-link { color: var(--ch1); text-decoration: none; border: 1px solid var(--ch1-deep);
  background: transparent; display: inline-flex; align-items: center; gap: .3rem; }
.chip-link:hover { background: rgba(60,224,200,.14); }
.dr-sec h3 { font-size: .82rem; font-weight: 600; margin: 0 0 .6rem; text-transform: uppercase;
  letter-spacing: .4px; color: var(--p-surface-200); }
.dr-sec h3 .sub, .sub { text-transform: none; letter-spacing: 0; font-weight: 400;
  color: var(--p-surface-400); font-size: .92em; }
.op-tabs { display: flex; gap: .35rem; flex-wrap: wrap; margin-bottom: .5rem; }
.op-tab { font-size: .72rem; padding: .25rem .7rem; border-radius: 6px; cursor: pointer;
  background: var(--p-surface-800); color: var(--p-surface-300); border: 1px solid transparent; }
.op-tab.sel { background: rgba(60,224,200,.14); color: var(--ch1); border-color: var(--ch1-deep); }
.op-meta { display: flex; gap: 1.1rem; flex-wrap: wrap; font-size: .72rem; color: var(--p-surface-300);
  margin-bottom: .5rem; }
.dr-empty { padding: 1.2rem; text-align: center; color: var(--p-surface-400); font-size: .8rem;
  border: 1px dashed var(--p-surface-700); border-radius: 8px; }
.type-cell { display: inline-flex; align-items: center; gap: .35rem; text-transform: capitalize; }
.passive-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--ch1); display: inline-block; }
.mpn-clickable { color: var(--ch1); cursor: pointer; text-decoration: underline dotted; text-underline-offset: 2px; }
.mpn-clickable:hover { text-decoration: underline; }
.rated { color: var(--p-surface-500); font-size: .9em; margin-left: .25rem; }
.m-good { color: var(--ch1); }
.m-warn { color: #f5bf50; }
.m-bad { color: var(--fault, #ff5d55); font-weight: 600; }
.bom-detail { padding: .6rem .8rem; display: flex; flex-direction: column; gap: .6rem; }
.bom-grid { display: grid; grid-template-columns: repeat(2, minmax(180px, 1fr)); gap: .4rem 1.4rem;
  font-size: .76rem; }
.bom-grid .lbl { display: block; color: var(--p-surface-400); font-size: .66rem;
  text-transform: uppercase; letter-spacing: .3px; }
.raw-toggle { display: inline-flex; align-items: center; gap: .4rem; cursor: pointer;
  font-size: .8rem; color: var(--ch1); user-select: none; }
.raw-toggle:hover { color: var(--p-surface-100); }
.report-html { margin-top: .7rem; max-height: 60vh; overflow: auto; }
.muted { color: var(--p-surface-400); font-size: .76rem; }
</style>
