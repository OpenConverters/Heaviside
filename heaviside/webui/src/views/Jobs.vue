<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import ResultViewer from '../components/ResultViewer.vue'
import { api } from '../api.js'
import { jobSeverity } from '../status.js'

const jobs = ref([])
const loading = ref(false)
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

onMounted(() => { load(); timer = setInterval(load, 3000) })
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="panel">
    <div style="display:flex; align-items:center; gap:.6rem; margin-bottom:.6rem">
      <Button label="Refresh" icon="pi pi-refresh" size="small" severity="secondary" outlined @click="load" />
      <span class="stage-line">auto-refreshing every 3s</span>
    </div>
    <DataTable :value="jobs" :loading="loading" size="small" stripedRows>
      <Column field="job_id" header="ID" bodyClass="col-mpn" />
      <Column field="kind" header="Kind" sortable />
      <Column field="status" header="Status" sortable>
        <template #body="{ data }"><Tag :severity="jobSeverity(data.status)" :value="data.status" /></template>
      </Column>
      <Column field="summary" header="Detail / progress" />
      <Column header="">
        <template #body="{ data }">
          <Button v-if="data.status === 'done'" label="View" icon="pi pi-eye" text size="small" @click="view(data)" />
          <Button :label="isActive(data.status) ? 'Cancel' : 'Delete'" text size="small"
                  :severity="isActive(data.status) ? 'warn' : 'danger'" @click="act(data)" />
        </template>
      </Column>
      <template #empty><span class="muted">No jobs yet.</span></template>
    </DataTable>
  </div>

  <ResultViewer v-model:visible="viewer.visible" :title="viewer.title"
                :kind="viewer.kind" :result="viewer.result" :loading="viewer.loading" />
</template>
