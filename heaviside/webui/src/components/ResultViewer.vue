<script setup>
import Dialog from 'primevue/dialog'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import { statusSeverity } from '../status.js'

defineProps({
  visible: Boolean, title: String, kind: String, result: Object, loading: Boolean,
})
defineEmits(['update:visible'])
</script>

<template>
  <Dialog :visible="visible" @update:visible="$emit('update:visible', $event)" modal
          maximizable :header="title" :style="{ width: 'min(960px, 95vw)' }"
          :pt="{ root: { class: 'om-dark' } }">
    <div v-if="loading">Loading…</div>
    <template v-else-if="kind === 'design'">
      <Tag v-if="result.verdict" :severity="result.verdict === 'pass' ? 'success' : 'warn'"
           :value="result.topology + ' · ' + result.verdict" />
      <div class="report-html" style="margin-top:.6rem; max-height:72vh" v-html="result.html"></div>
    </template>
    <template v-else-if="kind === 'crossref'">
      <Tag :severity="result.passed ? 'success' : 'warn'"
           :value="(result.passed ? 'PASS' : 'REVIEW') + ' → ' + result.target_manufacturer" />
      <Tag v-if="result.coverage_pct != null" severity="info" style="margin-left:.5rem"
           :value="'coverage ' + result.coverage_substituted + '/' + result.coverage_total + ' (' + result.coverage_pct + '%)'" />
      <DataTable :value="result.components" size="small" stripedRows removableSort style="margin-top:.6rem">
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
      <ul v-if="result.diagnostics?.length" class="diag">
        <li v-for="(dg, i) in result.diagnostics" :key="i">{{ dg }}</li>
      </ul>
    </template>
    <div v-else>No viewable result.</div>
  </Dialog>
</template>
