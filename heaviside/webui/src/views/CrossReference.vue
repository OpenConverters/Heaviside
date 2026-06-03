<script setup>
import { ref, onMounted } from 'vue'
import Select from 'primevue/select'
import SelectButton from 'primevue/selectbutton'
import Textarea from 'primevue/textarea'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import ProgressBar from 'primevue/progressbar'
import Message from 'primevue/message'
import { api, pollJob } from '../api.js'
import { statusSeverity } from '../status.js'

const target = ref('Würth Elektronik')
const manufacturers = ref([])
const mode = ref('bom')
const modes = [
  { label: 'Paste BOM', value: 'bom' },
  { label: 'From URL', value: 'url' },
  { label: 'Upload PDF', value: 'pdf' },
]
const bomText = ref('')
const url = ref('')
const pdfFile = ref(null)
const running = ref(false)
const status = ref('')
const result = ref(null)
const error = ref(null)

onMounted(async () => {
  try {
    const list = (await api.manufacturers()).manufacturers || []
    manufacturers.value = list.map((m) => m.name)
    const w = list.find((m) => /w[uü]rth/i.test(m.name))
    target.value = w ? w.name : list[0]?.name || target.value
  } catch (e) { /* dropdown stays empty */ }
})

function onPdf(e) { pdfFile.value = e.target.files?.[0] || null }

async function run() {
  error.value = null; result.value = null; running.value = true; status.value = 'submitting…'
  try {
    let job_id
    if (mode.value === 'pdf') {
      if (!pdfFile.value) throw new Error('choose a PDF first')
      ;({ job_id } = await api.submitCrossrefPdf(pdfFile.value, target.value))
    } else if (mode.value === 'url') {
      if (!url.value.trim()) throw new Error('enter a design URL first')
      ;({ job_id } = await api.submitCrossrefUrl({ url: url.value.trim(), target_manufacturer: target.value }))
    } else {
      let bom
      try { bom = JSON.parse(bomText.value) } catch (e) { throw new Error('BOM is not valid JSON') }
      ;({ job_id } = await api.submitCrossref({ source_bom: bom, target_manufacturer: target.value }))
    }
    result.value = await pollJob(job_id, (j) => { status.value = j.progress || j.status })
    status.value = 'done'
  } catch (e) { error.value = String(e); status.value = '' }
  finally { running.value = false }
}
</script>

<template>
  <div class="panel">
    <div class="grid3">
      <div class="field">
        <label class="fld-label">Target manufacturer</label>
        <Select v-model="target" :options="manufacturers" filter
                placeholder="select a vendor" />
      </div>
      <div class="field" style="grid-column: span 2">
        <label class="fld-label">Input mode</label>
        <SelectButton v-model="mode" :options="modes" optionLabel="label" optionValue="value" />
      </div>
    </div>

    <div v-if="mode === 'bom'" class="field" style="margin-top:.4rem">
      <label class="fld-label">Source BOM (JSON list of components)</label>
      <Textarea v-model="bomText" rows="8" style="width:100%"
                placeholder='[{"ref_des":"L1","component_type":"magnetic","value":"4.7uH"}]' />
    </div>
    <div v-else-if="mode === 'url'" class="field" style="margin-top:.4rem">
      <label class="fld-label">Reference-design URL</label>
      <InputText v-model="url" style="width:100%"
                 placeholder="https://www.ti.com/lit/… or a Murata/Vishay app-note link" />
      <p class="muted" style="font-size:.8rem">Downloads the page or PDF, reverse-engineers the design, simulates, then cross-references its BOM.</p>
    </div>
    <div v-else class="field" style="margin-top:.4rem">
      <label class="fld-label">Reference-design PDF</label>
      <input type="file" accept="application/pdf" @change="onPdf" />
      <p class="muted" style="font-size:.8rem">Reverse-engineers the PDF, simulates, then cross-references the extracted BOM.</p>
    </div>

    <div style="margin-top:.9rem; display:flex; gap:.5rem; align-items:center">
      <Button label="Cross-reference" icon="pi pi-sync" :loading="running" @click="run" />
      <span v-if="status && !running" class="stage-line">{{ status }}</span>
    </div>
    <div v-if="running" style="margin-top:.7rem">
      <div class="stage-line" style="margin-bottom:.3rem">{{ status }}</div>
      <ProgressBar mode="indeterminate" style="height:8px" />
    </div>
  </div>

  <div v-if="result" class="panel">
    <Tag :severity="result.passed ? 'success' : 'warn'"
         :value="(result.passed ? 'PASS' : 'REVIEW') + ' · ' + result.components.length + ' components → ' + result.target_manufacturer" />
    <Tag v-if="result.coverage_pct != null" severity="info" style="margin-left:.5rem"
         :value="'coverage ' + result.coverage_substituted + '/' + result.coverage_total + ' (' + result.coverage_pct + '%)'" />
    <DataTable :value="result.components" size="small" stripedRows removableSort style="margin-top:.7rem">
      <Column field="ref_des" header="Ref" sortable />
      <Column field="component_type" header="Type" sortable />
      <Column field="original_mpn" header="Original" sortable bodyClass="col-mpn" />
      <Column field="substitute_mpn" header="Substitute" sortable bodyClass="col-mpn" />
      <Column field="status" header="Status" sortable>
        <template #body="{ data }">
          <Tag :severity="statusSeverity(data.status)" :value="data.status" />
        </template>
      </Column>
    </DataTable>
    <Message v-if="!result.components.length" severity="warn" style="margin-top:.6rem">
      No substitutions were produced — see diagnostics.
    </Message>
    <ul v-if="result.diagnostics?.length" class="diag">
      <li v-for="(dg, i) in result.diagnostics" :key="i">{{ dg }}</li>
    </ul>
  </div>
  <Message v-if="error" severity="error" style="margin-top:1rem">{{ error }}</Message>
</template>
