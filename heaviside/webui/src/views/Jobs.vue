<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import ResultViewer from '../components/ResultViewer.vue'
import PipelineFlow from '../components/PipelineFlow.vue'
import { api } from '../api.js'
import { jobSeverity } from '../status.js'

// open-job: a job_id from a #/jobs/<id> deep link — auto-opened on mount.
const props = defineProps({ openJob: { type: String, default: null } })

const jobs = ref([])
const loading = ref(false)
const expanded = ref({})        // PrimeVue row expansion state (keyed by job_id)
let timer = null

const viewer = ref({ visible: false, loading: false, kind: '', title: '', result: {}, jobId: '' })

// The job we want kept expanded (set when navigating here from Designer/CR).
// Persists until the user manually collapses it.
const trackedJob = ref(null)

async function load() {
  loading.value = true
  try {
    jobs.value = (await api.jobs()).jobs
    // Auto-expand the tracked job as soon as it appears in the list.
    if (trackedJob.value) {
      const job = jobs.value.find((j) => j.job_id === trackedJob.value)
      if (job && !expanded.value[job.job_id]) expanded.value[job.job_id] = job
    }
  } catch (e) { /* keep prior */ }
  finally { loading.value = false }
}
const isActive = (s) => s === 'queued' || s === 'running'
async function act(job) {
  if (isActive(job.status)) await api.cancelJob(job.job_id)
  else await api.deleteJob(job.job_id)
  load()
}
// Open a result viewer for a finished job.
async function viewById(jobId, kind) {
  if (viewer.value.visible && viewer.value.jobId === jobId) return
  viewer.value = { visible: true, loading: true, result: {}, jobId, kind: 'crossref',
                   title: (kind || 'job') + ' · ' + jobId }
  if (location.hash !== `#/jobs/${jobId}`) location.hash = `#/jobs/${jobId}`
  try {
    const job = await api.job(jobId)
    const k = kind || job.kind || ''
    viewer.value.kind = k === 'design' ? 'design' : 'crossref'
    viewer.value.title = (k || 'job') + ' · ' + jobId
    viewer.value.result = job.result || {}
  } finally { viewer.value.loading = false }
}
const view = (job) => viewById(job.job_id, job.kind)

// Track the job from a deep link: expand it in the table (not the viewer —
// it may still be running). The viewer is opened by the user via "View".
function trackJob(id) {
  if (!id) return
  trackedJob.value = id
  if (location.hash !== `#/jobs/${id}`) location.hash = `#/jobs/${id}`
  // Expand immediately if the job is already in the list.
  const job = jobs.value.find((j) => j.job_id === id)
  if (job) expanded.value[job.job_id] = job
}

// When the viewer closes, drop the per-job id from the URL (back to #/jobs).
watch(() => viewer.value.visible, (vis) => {
  if (!vis && location.hash.startsWith('#/jobs/')) location.hash = '#/jobs'
})
// React to the openJob prop (navigated here from Designer or CR).
watch(() => props.openJob, (id) => { if (id) trackJob(id) }, { immediate: false })

// Poll faster (1.5s) so the in-flight pipeline animates.
onMounted(() => {
  load()
  timer = setInterval(load, 1500)
  if (props.openJob) trackJob(props.openJob)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="panel">
    <div style="display:flex; align-items:center; gap:.6rem; margin-bottom:.6rem">
      <Button label="Refresh" icon="pi pi-refresh" size="small" severity="secondary" outlined @click="load" />
      <span class="stage-line">click a row to watch its pipeline · auto-refreshing</span>
    </div>
    <DataTable :value="jobs" size="small" stripedRows
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
          <a v-if="data.status === 'done'" :href="api.reportPdfUrl(data.job_id)"
             target="_blank" @click.stop class="pdf-link mono">PDF</a>
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
                :kind="viewer.kind" :result="viewer.result" :loading="viewer.loading"
                :pdf-url="viewer.jobId ? api.reportPdfUrl(viewer.jobId) : ''" />
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
.pdf-link { font-size: .7rem; color: var(--ch1); text-decoration: none; padding: 0 .3rem;
  border: 1px solid var(--ch1-deep); border-radius: 5px; }
.pdf-link:hover { background: rgba(60,224,200,.14); }
</style>
