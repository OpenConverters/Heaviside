<script setup>
import { ref, onMounted } from 'vue'
import Select from 'primevue/select'
import SelectButton from 'primevue/selectbutton'
import Textarea from 'primevue/textarea'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Message from 'primevue/message'
import { api, myJobs } from '../api.js'

const target = ref('Würth Elektronik')
const manufacturers = ref([])
const mode = ref('bom')
const modes = [
  { label: 'Paste BOM', value: 'bom' },
  { label: 'Upload BOM (CSV/XLSX)', value: 'csv' },
  { label: 'From URL', value: 'url' },
  { label: 'Upload PDF', value: 'pdf' },
]
const bomText = ref('')
const url = ref('')
const pdfFile = ref(null)
const bomFile = ref(null)
const running = ref(false)
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
function onBom(e) { bomFile.value = e.target.files?.[0] || null }

async function run() {
  error.value = null; running.value = true
  try {
    let job_id
    if (mode.value === 'pdf') {
      if (!pdfFile.value) throw new Error('choose a PDF first')
      ;({ job_id } = await api.submitCrossrefPdf(pdfFile.value, target.value))
    } else if (mode.value === 'csv') {
      if (!bomFile.value) throw new Error('choose a CSV or Excel BOM file first')
      ;({ job_id } = await api.submitCrossrefBom(bomFile.value, target.value))
    } else if (mode.value === 'url') {
      if (!url.value.trim()) throw new Error('enter a design URL first')
      ;({ job_id } = await api.submitCrossrefUrl({ url: url.value.trim(), target_manufacturer: target.value }))
    } else {
      const text = bomText.value.trim()
      if (!text) throw new Error('Paste a BOM first — a JSON list, CSV/TSV rows, or bare part numbers.')
      // Only a JSON *array* is a JSON BOM — bare numeric part numbers
      // (e.g. Würth's 850617021001) also parse as JSON, but as numbers.
      let bom = null
      try { bom = JSON.parse(text) } catch (e) { /* not JSON — CSV or bare refs */ }
      if (Array.isArray(bom)) {
        ;({ job_id } = await api.submitCrossref({ source_bom: bom, target_manufacturer: target.value }))
      } else {
        // Bare part numbers (one per line, no delimiters, no header line) get
        // a synthesized MPN header; anything else is passed through as pasted
        // CSV/TSV. Both go through the same parser as an uploaded file.
        const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
        const bareRefs = lines.every((l) => !/[,;\t ]/.test(l)) &&
          !/^(mpn|part|ref|value|manufacturer|category|type|qty)/i.test(lines[0])
        const csv = bareRefs ? 'MPN\n' + lines.join('\n') : text
        const f = new File([csv], 'pasted_bom.csv', { type: 'text/csv' })
        ;({ job_id } = await api.submitCrossrefBom(f, target.value))
      }
    }
    myJobs.add(job_id)
    location.hash = `#/jobs/${job_id}`
  } catch (e) { error.value = e?.message ?? String(e) }
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
      <label class="fld-label">Source BOM (part numbers, CSV/TSV rows, or JSON list)</label>
      <Textarea v-model="bomText" rows="8" style="width:100%"
                placeholder="CRCW060310K0FKED — bare part numbers (one per line), CSV rows with a header, or a JSON component list" />
      <p class="muted" style="font-size:.8rem">Paste bare part numbers (one per line), CSV/TSV rows with a header line (recognised columns: MPN / Part Number, Manufacturer, Category/Type, Ref, Value, Voltage), or a JSON component list.</p>
    </div>
    <div v-else-if="mode === 'csv'" class="field" style="margin-top:.4rem">
      <label class="fld-label">BOM file (CSV, TSV, or .xlsx)</label>
      <input type="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm,text/csv" @change="onBom" />
      <p class="muted" style="font-size:.8rem">A bare component list — no reference design needed. Recognised columns: MPN / Part Number, Manufacturer, Category/Type, Ref, Value, Voltage. Each part is cross-referenced to the target manufacturer.</p>
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
      <span v-if="running" class="stage-line">submitting…</span>
    </div>
  </div>
  <Message v-if="error" severity="error" style="margin-top:1rem">{{ error }}</Message>
</template>
