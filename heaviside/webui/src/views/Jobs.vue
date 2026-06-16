<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import ResultViewer from '../components/ResultViewer.vue'
import PipelineFlow from '../components/PipelineFlow.vue'
import { api } from '../api.js'
import { jobSeverity } from '../status.js'

const jobs = ref([])
const loading = ref(false)
const expanded = ref({})        // PrimeVue row expansion state (keyed by job_id)
let timer = null

const viewer = ref({ visible: false, loading: false, kind: '', title: '', result: {} })

async function load() {
  loading.value = true
  try { jobs.value = (await api.jobs()).jobs } catch (e) { /* keep prior */ }
  finally { loading.value = false }
}
const isActive = (s) => s === 'queued' || s === 'running'
async function act(job) {
  if (isActive(job.status)) await api.cancelJob(job.job_id)
  else await api.deleteJob(job.job_id)
  load()
}
async function view(job) {
  viewer.value = {
    visible: true, loading: true, result: {},
    kind: job.kind === 'design' ? 'design' : 'crossref',
    title: job.kind + ' · ' + job.job_id,
  }
  try { viewer.value.result = (await api.job(job.job_id)).result || {} }
  finally { viewer.value.loading = false }
}

// Poll faster (1.5s) so the in-flight pipeline animates; the /jobs list already
// carries each job's stages, so no extra request is needed for the flow view.
onMounted(() => { load(); timer = setInterval(load, 1500) })
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="panel">
    <div style="display:flex; align-items:center; gap:.6rem; margin-bottom:.6rem">
      <Button label="Refresh" icon="pi pi-refresh" size="small" severity="secondary" outlined @click="load" />
      <span class="stage-line">click a row to watch its pipeline · auto-refreshing</span>
    </div>
    <DataTable :value="jobs" :loading="loading" size="small" stripedRows
               v-model:expandedRows="expanded" dataKey="job_id"
               @rowClick="(e) => (expanded[e.data.job_id] = expanded[e.data.job_id] ? undefined : e.data)"
               rowHover>
      <Column expander style="width:2.5rem" />
      <Column field="job_id" header="ID" bodyClass="col-mpn" />
      <Column field="kind" header="Kind" sortable />
      <Column field="status" header="Status" sortable>
        <template #body="{ data }"><Tag :severity="jobSeverity(data.status)" :value="data.status" /></template>
      </Column>
      <Column header="Pipeline">
        <template #body="{ data }">
          <span class="mono mini-stages" v-if="data.stages?.length">
            {{ data.stages.filter(s => s.status === 'done').length }}/{{ data.stages.length }}
            <span v-for="(s, i) in data.stages" :key="i" class="dot" :class="`d-${s.status}`" />
          </span>
          <span v-else class="muted">—</span>
        </template>
      </Column>
      <Column field="summary" header="Detail / progress" />
      <Column header="">
        <template #body="{ data }">
          <Button v-if="data.status === 'done'" label="View" icon="pi pi-eye" text size="small" @click.stop="view(data)" />
          <Button :label="isActive(data.status) ? 'Cancel' : 'Delete'" text size="small"
                  :severity="isActive(data.status) ? 'warn' : 'danger'" @click.stop="act(data)" />
        </template>
      </Column>
      <template #expansion="{ data }">
        <PipelineFlow :stages="data.stages || []" :status="data.status" />
      </template>
      <template #empty><span class="muted">No jobs yet.</span></template>
    </DataTable>
  </div>

  <ResultViewer v-model:visible="viewer.visible" :title="viewer.title"
                :kind="viewer.kind" :result="viewer.result" :loading="viewer.loading" />
</template>

<style scoped>
.mini-stages { display: inline-flex; align-items: center; gap: 3px; font-size: .68rem;
  color: var(--p-surface-400); }
.mini-stages .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block;
  background: var(--p-surface-700); }
.dot.d-done { background: var(--ch1); }
.dot.d-running { background: var(--ch1); box-shadow: 0 0 6px var(--ch1); animation: blink 1s infinite; }
.dot.d-error { background: var(--fault); }
@keyframes blink { 50% { opacity: .35; } }
</style>
