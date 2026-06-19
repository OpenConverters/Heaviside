<script setup>
import { ref } from 'vue'
import Dialog from 'primevue/dialog'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import { statusSeverity } from '../status.js'
import { useDatasheet, inferCategory } from '../composables/useDatasheet.js'

defineProps({
  visible: Boolean, title: String, kind: String, result: Object, loading: Boolean,
  pdfUrl: { type: String, default: '' },
})
defineEmits(['update:visible'])

const { openDatasheet } = useDatasheet()

const expanded = ref({})
const verdictClass = (v) => ({
  exact: 'v-good', same: 'v-good', exceeds: 'v-good',
  differs: 'v-warn', lower: 'v-bad', 'n/a': 'v-muted',
}[v] || 'v-muted')

function dsOpen(mpn, componentType) {
  const cat = inferCategory(componentType)
  if (mpn && cat) openDatasheet(mpn, cat)
}
</script>

<template>
  <Dialog :visible="visible" @update:visible="$emit('update:visible', $event)" modal
          maximizable :style="{ width: '95vw', height: '92vh' }"
          :contentStyle="{ height: '100%' }" :pt="{ root: { class: 'om-dark' } }">
    <template #header>
      <div class="rv-head">
        <span class="rv-title">{{ title }}</span>
        <a v-if="pdfUrl" :href="pdfUrl" target="_blank" class="rv-pdf">
          <i class="pi pi-download" /> Download PDF
        </a>
      </div>
    </template>
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
      <DataTable :value="result.components" size="small" stripedRows removableSort
                 style="margin-top:.6rem" v-model:expandedRows="expanded" dataKey="ref_des"
                 @rowClick="(e) => (expanded[e.data.ref_des] = expanded[e.data.ref_des] ? undefined : e.data)"
                 rowHover>
        <Column expander style="width:2.5rem" />
        <Column field="ref_des" header="Ref" sortable />
        <Column field="component_type" header="Type" sortable />
        <Column field="original_mpn" header="Original" sortable bodyClass="col-mpn">
          <template #body="{ data }">
            <span class="mpn-chip"
                  :class="{ 'mpn-clickable': inferCategory(data.component_type) }"
                  @click.stop="dsOpen(data.original_mpn, data.component_type)">
              {{ data.original_mpn || '—' }}
            </span>
          </template>
        </Column>
        <Column field="substitute_mpn" header="Substitute" sortable bodyClass="col-mpn">
          <template #body="{ data }">
            <span class="mpn-chip"
                  :class="{ 'mpn-clickable': inferCategory(data.component_type) }"
                  @click.stop="dsOpen(data.substitute_mpn, data.component_type)">
              {{ data.substitute_mpn || '—' }}
            </span>
          </template>
        </Column>
        <Column field="status" header="Status" sortable>
          <template #body="{ data }">
            <Tag :severity="statusSeverity(data.status)" :value="data.status" />
          </template>
        </Column>
        <Column header="Why">
          <template #body="{ data }">
            <span class="why-line">{{ data.match_detail?.why || data.notes || '—' }}</span>
          </template>
        </Column>
        <template #expansion="{ data }">
          <div class="mx-detail">
            <div class="mx-why-row">
              <div class="mx-why"><b>Why {{ data.status }}:</b> {{ data.match_detail?.why || data.notes }}</div>
              <div v-if="inferCategory(data.component_type)" class="mx-ds-btns">
                <Button v-if="data.original_mpn" icon="pi pi-file" :label="data.original_mpn" size="small"
                        text severity="secondary" @click="dsOpen(data.original_mpn, data.component_type)" />
                <Button v-if="data.substitute_mpn" icon="pi pi-file" :label="data.substitute_mpn" size="small"
                        text severity="secondary" @click="dsOpen(data.substitute_mpn, data.component_type)" />
              </div>
            </div>
            <table v-if="data.match_detail?.params?.length" class="mx-params">
              <thead><tr><th>Parameter</th><th>Original</th><th></th><th>Substitute</th><th>Verdict</th></tr></thead>
              <tbody>
                <tr v-for="(p, i) in data.match_detail.params" :key="i">
                  <td class="mx-name">{{ p.name }}</td>
                  <td class="mono">{{ p.original || '—' }}</td>
                  <td class="mx-arrow">→</td>
                  <td class="mono">{{ p.substitute || '—' }}</td>
                  <td><span class="v-chip" :class="verdictClass(p.verdict)">{{ p.verdict }}</span></td>
                </tr>
              </tbody>
            </table>
            <div v-if="data.guardrail_fires?.length" class="mx-guard">
              ⚠ guardrails: {{ data.guardrail_fires.join(', ') }}
            </div>
            <div v-if="data.notes && data.notes !== data.match_detail?.why" class="mx-notes">
              note: {{ data.notes }}
            </div>
          </div>
        </template>
      </DataTable>
      <ul v-if="result.diagnostics?.length" class="diag">
        <li v-for="(dg, i) in result.diagnostics" :key="i">{{ dg }}</li>
      </ul>
    </template>
    <div v-else>No viewable result.</div>
  </Dialog>
</template>

<style scoped>
.rv-head { display: flex; align-items: center; gap: 1rem; flex: 1; }
.rv-title { font-weight: 600; }
.rv-pdf { font-size: .74rem; color: var(--ch1); text-decoration: none; padding: .25rem .6rem;
  border: 1px solid var(--ch1-deep); border-radius: 6px; display: inline-flex; align-items: center; gap: .35rem; }
.rv-pdf:hover { background: rgba(60,224,200,.14); }
.why-line { font-size: .72rem; color: var(--p-surface-300); }
.mpn-chip { display: inline-block; }
.mpn-clickable { color: var(--ch1); cursor: pointer; text-decoration: underline dotted; text-underline-offset: 2px; }
.mpn-clickable:hover { text-decoration: underline; }
.mx-detail { padding: .5rem .8rem; font-size: .74rem; }
.mx-why-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; margin-bottom: .5rem; }
.mx-why { color: var(--p-surface-200); }
.mx-ds-btns { display: flex; gap: .2rem; flex-shrink: 0; }
.mx-params { border-collapse: collapse; width: auto; }
.mx-params th { text-align: left; font-weight: 600; color: var(--p-surface-400);
  padding: .15rem .7rem .15rem 0; font-size: .66rem; text-transform: uppercase; letter-spacing: .3px; }
.mx-params td { padding: .15rem .7rem .15rem 0; }
.mx-name { text-transform: capitalize; color: var(--p-surface-300); }
.mx-arrow { color: var(--p-surface-500); }
.v-chip { font-size: .64rem; padding: .05rem .45rem; border-radius: 6px; font-weight: 600; }
.v-good { background: rgba(60,224,200,.16); color: var(--ch1); }
.v-warn { background: rgba(245,191,80,.16); color: #f5bf50; }
.v-bad  { background: rgba(255,93,85,.16); color: var(--fault); }
.v-muted{ background: var(--p-surface-800); color: var(--p-surface-400); }
.mx-guard { margin-top: .5rem; color: #f5bf50; }
.mx-notes { margin-top: .35rem; color: var(--p-surface-400); font-style: italic; }
</style>
