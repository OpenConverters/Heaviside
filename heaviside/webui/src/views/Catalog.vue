<script setup>
import { ref, onMounted } from 'vue'
import Select from 'primevue/select'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import { useDatasheet } from '../composables/useDatasheet.js'
import { api } from '../api.js'

const { openDatasheet } = useDatasheet()

const categories = ['mosfets', 'diodes', 'capacitors', 'resistors', 'magnetics']
const category = ref('mosfets')
const q = ref('')
const rows = ref([])
const labels = ref(['', '', ''])
const count = ref(0)
const loading = ref(false)
const error = ref(null)

function openSheet(row) { openDatasheet(row.mpn, category.value) }

async function load() {
  loading.value = true; error.value = null
  try {
    const j = await api.catalog(category.value, q.value, 50)
    rows.value = j.rows; labels.value = j.param_labels; count.value = j.count
  } catch (e) { error.value = String(e); rows.value = [] }
  finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <div class="panel">
    <div class="grid3">
      <div class="field">
        <label class="fld-label">Category</label>
        <Select v-model="category" :options="categories" @change="load" />
      </div>
      <div class="field" style="grid-column: span 2">
        <label class="fld-label">Search (MPN or manufacturer)</label>
        <InputText v-model="q" style="width:100%" placeholder="e.g. Infineon, IRF, 744…" @keyup.enter="load" />
      </div>
    </div>
    <div style="display:flex; align-items:center; gap:.6rem; margin:.3rem 0 .7rem">
      <Button label="Search" icon="pi pi-search" size="small" :loading="loading" @click="load" />
      <span class="stage-line">{{ count }} matches · click any row for datasheet</span>
    </div>
    <DataTable :value="rows" :loading="loading" size="small" stripedRows removableSort
               paginator :rows="12" :rowsPerPageOptions="[12, 25, 50]"
               rowHover @rowClick="(e) => openSheet(e.data)">
      <Column field="mpn" header="Part Number" sortable bodyClass="col-mpn" />
      <Column field="manufacturer" header="Manufacturer" sortable />
      <Column field="tech" header="Tech" sortable />
      <Column field="p1" :header="labels[0]" sortable />
      <Column field="p2" :header="labels[1]" sortable />
      <Column field="p3" :header="labels[2]" sortable />
      <Column field="status" header="Status" sortable>
        <template #body="{ data }">
          <Tag :severity="data.status === 'production' ? 'success' : 'secondary'" :value="data.status || '—'" />
        </template>
      </Column>
      <Column header="">
        <template #body="{ data }">
          <Button icon="pi pi-file" text size="small" severity="secondary"
                  title="View datasheet" @click.stop="openSheet(data)" />
        </template>
      </Column>
    </DataTable>
    <Message v-if="error" severity="error" style="margin-top:.6rem">{{ error }}</Message>
  </div>
</template>
