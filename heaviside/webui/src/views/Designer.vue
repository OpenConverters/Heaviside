<script setup>
import { ref, computed, onMounted } from 'vue'
import InputNumber from 'primevue/inputnumber'
import SelectButton from 'primevue/selectbutton'
import Button from 'primevue/button'
import ProgressBar from 'primevue/progressbar'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import { api, pollJob } from '../api.js'

const step = ref(1)
const d = ref({
  vinMin: 9, vinNom: 12, vinMax: 16,
  outputs: [{ vout: 3.3, iout: 3 }],
  fswKhz: 500, ambient: 25, eff: 0.92,
  mode: 'ripple', ripple: 0.3, inductanceUh: 4.7, topology: null,
})
function addOutput() { d.value.outputs.push({ vout: 5, iout: 1 }) }
function removeOutput(i) { d.value.outputs.splice(i, 1) }
const totalPower = computed(() =>
  d.value.outputs.reduce((s, o) => s + (o.vout || 0) * (o.iout || 0), 0))
const multiOutput = computed(() => d.value.outputs.length > 1)
const modes = [
  { label: 'Ripple ratio', value: 'ripple' },
  { label: 'Known inductance', value: 'known' },
]
const topologies = ref([{ label: 'Auto', value: null }])
const running = ref(false)
const status = ref('')
const pct = ref(0)
const result = ref(null)
const error = ref(null)

onMounted(async () => {
  try {
    const list = await api.topologies()
    for (const t of list) topologies.value.push({ label: t.name, value: t.name })
  } catch (e) { /* Auto still available */ }
})

function buildSpec() {
  const spec = {
    inputVoltage: { minimum: d.value.vinMin, nominal: d.value.vinNom, maximum: d.value.vinMax },
    operatingPoints: [{
      outputVoltages: d.value.outputs.map((o) => o.vout),
      outputCurrents: d.value.outputs.map((o) => o.iout),
      switchingFrequency: d.value.fswKhz * 1000, ambientTemperature: d.value.ambient,
    }],
    efficiency: d.value.eff, diodeVoltageDrop: 0.7,
  }
  if (d.value.mode === 'known') spec.desiredInductance = d.value.inductanceUh * 1e-6
  else spec.currentRippleRatio = d.value.ripple
  return spec
}

async function run() {
  error.value = null; result.value = null; running.value = true
  status.value = 'submitting…'; pct.value = 0
  try {
    const body = { spec: buildSpec(), candidates_per_topology: 3 }
    if (d.value.topology) body.topologies = [d.value.topology]
    const { job_id } = await api.submitDesign(body)
    result.value = await pollJob(job_id, (j) => {
      status.value = j.progress || j.status
      const m = /^(\d+)%/.exec(j.progress || '')
      pct.value = m ? +m[1] : 0
    })
    status.value = 'done'; pct.value = 100
  } catch (e) { error.value = String(e); status.value = '' }
  finally { running.value = false }
}
</script>

<template>
  <div class="panel">
    <div class="steps">
      <div class="step" :class="{ active: step === 1, done: step > 1 }">
        <span class="num">1</span> Operating point
      </div>
      <span class="step-line"></span>
      <div class="step" :class="{ active: step === 2, done: step > 2 }">
        <span class="num">2</span> Inductor sizing
      </div>
      <span class="step-line"></span>
      <div class="step" :class="{ active: step === 3 }">
        <span class="num">3</span> Topology
      </div>
    </div>

    <!-- Step 1 -->
    <div v-show="step === 1">
      <div class="section-label">Input voltage</div>
      <div class="grid3">
        <div class="field"><label class="fld-label">Vin min (V)</label>
          <InputNumber v-model="d.vinMin" /></div>
        <div class="field"><label class="fld-label">Vin nom (V)</label>
          <InputNumber v-model="d.vinNom" /></div>
        <div class="field"><label class="fld-label">Vin max (V)</label>
          <InputNumber v-model="d.vinMax" /></div>
      </div>
      <div class="section-label" style="margin-top:1rem">
        Output rails
        <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:500">
          — {{ totalPower.toFixed(1) }} W total{{ multiOutput ? ' · multi-output' : '' }}</span>
      </div>
      <div v-for="(o, i) in d.outputs" :key="i" class="rail">
        <span class="rail-tag mono">OUT{{ i }}</span>
        <div class="field"><label class="fld-label">Vout (V)</label>
          <InputNumber v-model="o.vout" :minFractionDigits="1" :maxFractionDigits="2" /></div>
        <div class="field"><label class="fld-label">Iout (A)</label>
          <InputNumber v-model="o.iout" :maxFractionDigits="2" /></div>
        <Button v-if="d.outputs.length > 1" icon="pi pi-trash" text rounded severity="danger"
                aria-label="remove rail" @click="removeOutput(i)" />
      </div>
      <Button label="Add output rail" icon="pi pi-plus" text size="small" @click="addOutput" />
      <Message v-if="multiOutput" severity="info" style="margin-top:.6rem">
        Multiple rails describe a multi-output converter — pick an isolated topology
        (flyback, forward, isolated buck/-boost…) in step 3. Magnetic sizing &amp; the
        primary side use the full {{ totalPower.toFixed(1) }} W; per-secondary component
        selection is summarised on the main rail.
      </Message>

      <div class="section-label" style="margin-top:1rem">Switching</div>
      <div class="grid4">
        <div class="field"><label class="fld-label">fsw (kHz)</label>
          <InputNumber v-model="d.fswKhz" /></div>
        <div class="field"><label class="fld-label">Efficiency target</label>
          <InputNumber v-model="d.eff" :minFractionDigits="2" :maxFractionDigits="3" /></div>
      </div>
      <div style="margin-top:1.1rem">
        <Button label="Next" icon="pi pi-arrow-right" iconPos="right" @click="step = 2" />
      </div>
    </div>

    <!-- Step 2 -->
    <div v-show="step === 2">
      <div class="section-label">How should the main inductor be sized?</div>
      <SelectButton v-model="d.mode" :options="modes" optionLabel="label" optionValue="value" />
      <div class="grid4" style="margin-top:1rem">
        <div class="field" v-if="d.mode === 'ripple'">
          <label class="fld-label">Current ripple ratio</label>
          <InputNumber v-model="d.ripple" :minFractionDigits="2" :maxFractionDigits="2" :step="0.05" showButtons />
        </div>
        <div class="field" v-else>
          <label class="fld-label">Desired inductance (µH)</label>
          <InputNumber v-model="d.inductanceUh" :minFractionDigits="1" :maxFractionDigits="2" />
        </div>
        <div class="field"><label class="fld-label">Ambient (°C)</label>
          <InputNumber v-model="d.ambient" /></div>
      </div>
      <p class="muted" style="font-size:.82rem">
        Ripple ratio lets MKF size the inductance from the operating point; “known inductance”
        passes your value straight to the magnetic designer.
      </p>
      <div style="margin-top:1.1rem; display:flex; gap:.5rem">
        <Button label="Back" icon="pi pi-arrow-left" severity="secondary" outlined @click="step = 1" />
        <Button label="Next" icon="pi pi-arrow-right" iconPos="right" @click="step = 3" />
      </div>
    </div>

    <!-- Step 3 -->
    <div v-show="step === 3">
      <div class="section-label">Topology <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:500">— pick one, or let the screen choose</span></div>
      <div class="topo-grid">
        <span v-for="t in topologies" :key="String(t.value)" class="chip"
              :class="{ sel: d.topology === t.value }" @click="d.topology = t.value">
          <span class="chip-dot" v-if="t.value !== null"></span>
          {{ t.value === null ? '✦ Auto-select' : t.label }}
        </span>
      </div>
      <div style="margin-top:1.2rem; display:flex; gap:.5rem; align-items:center">
        <Button label="Back" icon="pi pi-arrow-left" severity="secondary" outlined @click="step = 2" />
        <Button label="Design converter" icon="pi pi-cog" :loading="running" @click="run" />
        <span v-if="status && !running" class="stage-line">{{ status }}</span>
      </div>
      <div v-if="running" style="margin-top:.8rem">
        <div class="stage-line" style="margin-bottom:.3rem">{{ status }}</div>
        <ProgressBar v-if="pct > 0" :value="pct" />
        <ProgressBar v-else mode="indeterminate" style="height:8px" />
      </div>
    </div>
  </div>

  <div v-if="result" class="panel">
    <Tag v-if="result.verdict" :severity="result.verdict === 'pass' ? 'success' : 'warn'"
         :value="result.topology + ' · ' + result.verdict" />
    <span v-if="result.alternatives?.length > 1" class="stage-line" style="margin-left:.5rem">
      {{ result.alternatives.length }} topologies evaluated
    </span>
    <div class="report-html" style="margin-top:.7rem" v-html="result.html"></div>
  </div>
  <Message v-if="error" severity="error" style="margin-top:1rem">{{ error }}</Message>
</template>
