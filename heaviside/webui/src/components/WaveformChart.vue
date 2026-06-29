<script setup>
import { computed } from 'vue'

// One simulated operating-point trace: inductor / primary-winding current (and,
// when present, voltage) vs. time, from PyOM's ngspice. Rendered as inline SVG
// so the report needs no charting dependency. Dual axis: current on the left
// (cyan), voltage on the right (amber).
const props = defineProps({
  wf: { type: Object, required: true },     // {label, time_s, current_a, voltage_v}
  height: { type: Number, default: 200 },
})

const W = 720
const PAD = { l: 56, r: 56, t: 14, b: 30 }

// SI-ish compact formatter for axis ticks / readouts.
function fmt(v, unit) {
  if (v == null || !isFinite(v)) return '—'
  const a = Math.abs(v)
  let s = v, p = ''
  if (a >= 1e3) { s = v / 1e3; p = 'k' }
  else if (a >= 1) { s = v; p = '' }
  else if (a >= 1e-3) { s = v * 1e3; p = 'm' }
  else if (a >= 1e-6) { s = v * 1e6; p = 'µ' }
  else if (a >= 1e-9) { s = v * 1e9; p = 'n' }
  else if (a !== 0) { s = v * 1e12; p = 'p' }
  return `${(+s.toPrecision(3))} ${p}${unit}`
}

const H = computed(() => props.height)
const plotW = W - PAD.l - PAD.r
const plotH = computed(() => H.value - PAD.t - PAD.b)

const t = computed(() => props.wf?.time_s || [])
const cur = computed(() => props.wf?.current_a || [])
const volt = computed(() => (Array.isArray(props.wf?.voltage_v) ? props.wf.voltage_v : null))

function extent(arr) {
  let lo = Infinity, hi = -Infinity
  for (const v of arr) { if (v < lo) lo = v; if (v > hi) hi = v }
  if (!isFinite(lo)) return [0, 1]
  if (lo === hi) { lo -= 1; hi += 1 }
  return [lo, hi]
}

const tExt = computed(() => extent(t.value))
const cExt = computed(() => extent(cur.value))
const vExt = computed(() => (volt.value ? extent(volt.value) : null))

function path(arr, ext) {
  const [tlo, thi] = tExt.value
  const [lo, hi] = ext
  const n = Math.min(t.value.length, arr.length)
  if (n < 2) return ''
  let d = ''
  for (let i = 0; i < n; i++) {
    const x = PAD.l + ((t.value[i] - tlo) / (thi - tlo)) * plotW
    const y = PAD.t + (1 - (arr[i] - lo) / (hi - lo)) * plotH.value
    d += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1) + ' '
  }
  return d
}

const curPath = computed(() => path(cur.value, cExt.value))
const voltPath = computed(() => (volt.value ? path(volt.value, vExt.value) : ''))
const gridY = computed(() => [0, 0.25, 0.5, 0.75, 1].map((f) => PAD.t + f * plotH.value))

// Readouts (peak / pp) shown in the legend.
const curPk = computed(() => Math.max(...cExt.value.map(Math.abs)))
const curPP = computed(() => cExt.value[1] - cExt.value[0])
const voltPk = computed(() => (vExt.value ? Math.max(...vExt.value.map(Math.abs)) : null))
const hasData = computed(() => t.value.length >= 2 && cur.value.length >= 2)
</script>

<template>
  <div class="wfc">
    <svg v-if="hasData" :viewBox="`0 0 ${W} ${H}`" width="100%" :height="H" class="wf-svg"
         preserveAspectRatio="xMidYMid meet">
      <!-- grid -->
      <line v-for="(gy, i) in gridY" :key="i" :x1="PAD.l" :x2="W - PAD.r" :y1="gy" :y2="gy"
            class="grid" />
      <!-- current trace (left axis) -->
      <path :d="curPath" class="trace-cur" fill="none" />
      <!-- voltage trace (right axis) -->
      <path v-if="voltPath" :d="voltPath" class="trace-volt" fill="none" />
      <!-- left-axis labels (current) -->
      <text :x="PAD.l - 6" :y="PAD.t + 4" class="ax ax-cur" text-anchor="end">{{ fmt(cExt[1], 'A') }}</text>
      <text :x="PAD.l - 6" :y="H - PAD.b" class="ax ax-cur" text-anchor="end">{{ fmt(cExt[0], 'A') }}</text>
      <!-- right-axis labels (voltage) -->
      <template v-if="vExt">
        <text :x="W - PAD.r + 6" :y="PAD.t + 4" class="ax ax-volt">{{ fmt(vExt[1], 'V') }}</text>
        <text :x="W - PAD.r + 6" :y="H - PAD.b" class="ax ax-volt">{{ fmt(vExt[0], 'V') }}</text>
      </template>
      <!-- time axis -->
      <text :x="PAD.l" :y="H - 8" class="ax ax-t" text-anchor="start">{{ fmt(tExt[0], 's') }}</text>
      <text :x="W - PAD.r" :y="H - 8" class="ax ax-t" text-anchor="end">{{ fmt(tExt[1], 's') }}</text>
    </svg>
    <div v-else class="wf-empty">No simulated waveform for this operating point.</div>

    <div v-if="hasData" class="wf-legend">
      <span class="lg lg-cur"><i></i> current — peak {{ fmt(curPk, 'A') }} · p-p {{ fmt(curPP, 'A') }}</span>
      <span v-if="voltPk != null" class="lg lg-volt"><i></i> voltage — peak {{ fmt(voltPk, 'V') }}</span>
    </div>
  </div>
</template>

<style scoped>
.wfc { width: 100%; }
.wf-svg { display: block; background: rgba(255,255,255,.015); border-radius: 8px; }
.grid { stroke: var(--p-surface-700); stroke-width: .5; opacity: .5; }
.trace-cur { stroke: var(--ch1, #3ce0c8); stroke-width: 1.6; }
.trace-volt { stroke: #f5bf50; stroke-width: 1.4; opacity: .9; stroke-dasharray: 4 2; }
.ax { font-size: 10px; fill: var(--p-surface-400); font-family: var(--font-mono, monospace); }
.ax-cur { fill: var(--ch1, #3ce0c8); }
.ax-volt { fill: #f5bf50; }
.wf-empty { padding: 1.4rem; text-align: center; color: var(--p-surface-400); font-size: .8rem; }
.wf-legend { display: flex; gap: 1.2rem; margin-top: .35rem; font-size: .7rem; color: var(--p-surface-300); }
.lg { display: inline-flex; align-items: center; gap: .35rem; }
.lg i { width: 14px; height: 2px; border-radius: 2px; display: inline-block; }
.lg-cur i { background: var(--ch1, #3ce0c8); }
.lg-volt i { background: #f5bf50; }
</style>
