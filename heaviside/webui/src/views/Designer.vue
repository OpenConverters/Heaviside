<script setup>
import { ref, computed, onMounted } from 'vue'
import InputNumber from 'primevue/inputnumber'
import Button from 'primevue/button'
import Message from 'primevue/message'
import { api, myJobs } from '../api.js'

const advanced = ref(false)
const d = ref({
  vinMin: 9, vinNom: 12, vinMax: 16,
  outputs: [{ vout: 3.3, iout: 3 }],
  ambient: 25, ripple: 0.3, topology: null,
})
function addOutput() { d.value.outputs.push({ vout: 5, iout: 1 }) }
function removeOutput(i) { d.value.outputs.splice(i, 1) }
const totalPower = computed(() =>
  d.value.outputs.reduce((s, o) => s + (o.vout || 0) * (o.iout || 0), 0))
const multiOutput = computed(() => d.value.outputs.length > 1)
const topologies = ref([{ label: 'Auto', value: null }])
const running = ref(false)
const error = ref(null)

onMounted(async () => {
  try {
    const list = await api.topologies()
    for (const t of list) topologies.value.push({ label: t.name.replace(/_/g, ' '), value: t.name })
  } catch (e) { /* Auto still available */ }
})

function buildSpec() {
  // Minimal input: Vin window + output rails + ambient. The designer chooses
  // the switching frequency (from the magnetic's total-loss sweep), sizes the
  // inductor, and seeds efficiency / diode drop — so those are no longer form
  // inputs. currentRippleRatio is the one optional magnetic knob MKF uses to
  // derive L from the operating point.
  return {
    inputVoltage: { minimum: d.value.vinMin, nominal: d.value.vinNom, maximum: d.value.vinMax },
    operatingPoints: [{
      outputVoltages: d.value.outputs.map((o) => o.vout),
      outputCurrents: d.value.outputs.map((o) => o.iout),
      ambientTemperature: d.value.ambient,
    }],
    currentRippleRatio: d.value.ripple,
  }
}

async function run() {
  error.value = null; running.value = true
  try {
    const body = { spec: buildSpec(), candidates_per_topology: 3 }
    if (d.value.topology) body.topologies = [d.value.topology]
    const { job_id } = await api.submitDesignClosedLoop(body)
    myJobs.add(job_id)
    location.hash = `#/jobs/${job_id}`
  } catch (e) { error.value = String(e) }
  finally { running.value = false }
}
</script>

<template>
  <div class="panel">
    <div class="hint mono">
      Give the converter its <b>input voltage</b> and <b>output rails</b> — the designer
      chooses the topology, switching frequency (swept against the magnetic’s total loss)
      and the inductor itself.
    </div>

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
      Multiple rails describe a multi-output converter — the designer will favour an
      isolated topology. Magnetic sizing &amp; the primary side use the full
      {{ totalPower.toFixed(1) }} W; per-secondary component selection is summarised on the
      main rail.
    </Message>

    <div class="section-label" style="margin-top:1rem">
      Topology
      <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:500">
        — auto-selected, or pin one</span>
    </div>
    <div class="topo-grid">
      <span v-for="t in topologies" :key="String(t.value)" class="chip"
            :class="{ sel: d.topology === t.value }" @click="d.topology = t.value">
        <span class="chip-dot" v-if="t.value !== null"></span>
        {{ t.value === null ? '✦ Auto-select' : t.label }}
      </span>
    </div>

    <div class="adv-toggle" @click="advanced = !advanced">
      <i :class="advanced ? 'pi pi-chevron-down' : 'pi pi-chevron-right'" />
      Advanced
      <span class="muted">— ambient {{ d.ambient }}°C</span>
    </div>
    <div v-show="advanced" class="grid4" style="margin-top:.5rem">
      <div class="field"><label class="fld-label">Ambient (°C)</label>
        <InputNumber v-model="d.ambient" /></div>
    </div>

    <div style="margin-top:1.3rem; display:flex; gap:.6rem; align-items:center">
      <Button label="Design converter" icon="pi pi-cog" :loading="running" @click="run" />
      <span v-if="running" class="stage-line">submitting…</span>
    </div>
  </div>
  <Message v-if="error" severity="error" style="margin-top:1rem">{{ error }}</Message>
</template>

<style scoped>
.hint {
  font-size: .76rem; line-height: 1.5; color: var(--p-surface-300);
  border-left: 2px solid var(--ch1-deep); padding: .1rem 0 .1rem .7rem;
  margin-bottom: 1.1rem;
}
.adv-toggle {
  margin-top: 1.1rem; display: inline-flex; align-items: center; gap: .4rem;
  font-size: .78rem; color: var(--ch1); cursor: pointer; user-select: none;
}
.adv-toggle i { font-size: .7rem; }
.adv-toggle:hover { color: var(--p-surface-100); }
.adv-toggle .muted { color: var(--p-surface-400); }
</style>
