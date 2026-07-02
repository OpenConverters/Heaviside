<script setup>
import { ref, computed, onMounted } from 'vue'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import { parseSheet, sectionVisible, fmtMTM, hasAny } from '../datasheetParsers.js'

const props = defineProps({ mpn: String, category: String })
const emit = defineEmits(['close'])

const loading = ref(false)
const error = ref(null)
const data = ref(null)

onMounted(async () => {
  if (!props.mpn || !props.category) return
  loading.value = true
  try {
    const r = await fetch(`/catalog/${props.category}/${encodeURIComponent(props.mpn)}/detail`)
    if (!r.ok) throw new Error(`${r.status}`)
    data.value = await r.json()
  } catch (e) { error.value = e?.message ?? String(e) }
  finally { loading.value = false }
})

const sheet = computed(() =>
  data.value ? parseSheet(data.value.category, data.value.data) : null)
</script>

<template>
  <div class="panel">
    <!-- Back nav -->
    <div class="detail-nav">
      <Button icon="pi pi-arrow-left" label="Back to catalog" text size="small"
              severity="secondary" @click="emit('close')" />
      <span v-if="sheet" class="detail-breadcrumb mono">
        {{ props.category }} / {{ sheet.title }}
      </span>
    </div>

    <div v-if="loading" class="detail-state stage-line">Loading…</div>
    <div v-else-if="error" class="detail-state" style="color:var(--fault)">{{ error }}</div>

    <template v-else-if="sheet">
      <!-- ── Header ── -->
      <div class="detail-header">
        <div class="detail-mfr mono">{{ sheet.manufacturer }}</div>
        <div class="detail-mpn">{{ sheet.title }}</div>
        <div class="detail-tags">
          <Tag v-for="t in sheet.tags" :key="t" :value="t" severity="secondary" class="detail-tag" />
          <Tag v-if="sheet.status" :value="sheet.status"
               :severity="sheet.status === 'production' ? 'success' : 'secondary'" class="detail-tag" />
        </div>
        <p v-if="sheet.description" class="detail-desc">{{ sheet.description }}</p>
        <a v-if="sheet.datasheetUrl" :href="sheet.datasheetUrl" target="_blank"
           rel="noopener" class="detail-pdf mono">
          ↗ Manufacturer Datasheet
        </a>
      </div>

      <!-- ── Headline KV strip ── -->
      <div class="detail-headline">
        <div v-for="h in sheet.headline" :key="h.sym" class="detail-kv">
          <span class="detail-sym mono">{{ h.sym }}</span>
          <span class="detail-kv-val mono">{{ h.val }}</span>
          <span class="detail-kv-label">{{ h.label }}</span>
        </div>
      </div>

      <!-- ── Parameter sections ── -->
      <div class="detail-sections">
        <div v-for="sec in sheet.sections.filter(sectionVisible)" :key="sec.title" class="detail-section">
          <div class="detail-sec-title">{{ sec.title }}</div>
          <table class="detail-table">
            <thead>
              <tr>
                <th class="col-param">Parameter</th>
                <th class="col-sym">Symbol</th>
                <th class="col-val num">Min</th>
                <th class="col-val num">Typ</th>
                <th class="col-val num">Max</th>
                <th class="col-note">Conditions</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="row in sec.rows" :key="row.param">
                <tr v-if="row.str !== undefined || hasAny(row.mtm, row.unit)">
                  <td class="col-param">{{ row.param }}</td>
                  <td class="col-sym mono">{{ row.sym }}</td>
                  <template v-if="row.str !== undefined">
                    <td class="col-val num" colspan="3">{{ row.str }}</td>
                  </template>
                  <template v-else>
                    <td v-for="v in [fmtMTM(row.mtm, row.unit).min, fmtMTM(row.mtm, row.unit).typ, fmtMTM(row.mtm, row.unit).max]"
                        :key="v" class="col-val num mono">{{ v }}</td>
                  </template>
                  <td class="col-note muted">{{ row.note ?? '' }}</td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <!-- Data arrived but no datasheet view exists for this category. -->
    <div v-else-if="data" class="detail-state muted">
      No datasheet view for {{ props.category }} yet — the part data is in the
      catalog API at <span class="mono">/catalog/{{ props.category }}/{{ props.mpn }}/detail</span>.
    </div>
  </div>
</template>

<style scoped>
.detail-nav {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1.2rem;
}
.detail-breadcrumb {
  font-size: .68rem;
  color: var(--p-surface-500);
  letter-spacing: .06em;
  text-transform: uppercase;
}
.detail-state {
  padding: 2rem 0;
  font-size: .85rem;
}

/* Header */
.detail-header { margin-bottom: 1.4rem; }
.detail-mfr {
  font-size: .72rem;
  color: var(--p-surface-400);
  letter-spacing: .06em;
  text-transform: uppercase;
  margin-bottom: .2rem;
}
.detail-mpn {
  font-size: 1.5rem;
  font-weight: 700;
  letter-spacing: .02em;
  color: var(--p-surface-50);
  line-height: 1.2;
  margin-bottom: .5rem;
}
.detail-tags { display: flex; flex-wrap: wrap; gap: .3rem; margin-bottom: .6rem; }
.detail-tag { font-size: .65rem !important; }
.detail-desc {
  margin: 0 0 .6rem;
  max-width: 68ch;
  font-size: .78rem;
  line-height: 1.5;
  color: var(--p-surface-300);
}
.detail-pdf { font-size: .72rem; color: var(--ch1); text-decoration: none; }
.detail-pdf:hover { text-decoration: underline; }

/* Headline */
.detail-headline {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: .6rem;
  background: var(--p-surface-800);
  border: 1px solid var(--p-surface-700);
  border-radius: 6px;
  padding: .9rem 1.2rem;
  margin-bottom: 1.8rem;
}
.detail-kv { display: flex; flex-direction: column; gap: .1rem; }
.detail-sym { font-size: .68rem; color: var(--p-surface-400); }
.detail-kv-val { font-size: 1.1rem; font-weight: 700; color: var(--ch1); line-height: 1.1; }
.detail-kv-label { font-size: .62rem; color: var(--p-surface-500); }

/* Sections — two-column on wide screens */
.detail-sections {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
  gap: 1.4rem 2rem;
  align-items: start;
}
.detail-section {}
.detail-sec-title {
  font-size: .65rem;
  font-weight: 700;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: var(--ch1);
  border-bottom: 1px solid var(--ch1-deep, #129e8b);
  padding-bottom: .3rem;
  margin-bottom: .5rem;
}

.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: .73rem;
  line-height: 1.4;
}
.detail-table th {
  text-align: left;
  font-size: .6rem;
  font-weight: 600;
  letter-spacing: .07em;
  text-transform: uppercase;
  color: var(--p-surface-500);
  border-bottom: 1px solid var(--p-surface-700);
  padding: .2rem .4rem .3rem;
}
.detail-table td {
  padding: .25rem .4rem;
  border-bottom: 1px solid var(--p-surface-800);
  color: var(--p-surface-200);
  vertical-align: middle;
}
.detail-table tbody tr:hover td { background: var(--p-surface-800); }
.col-param { min-width: 160px; }
.col-sym   { width: 70px; color: var(--p-surface-400); font-size: .68rem; }
.col-val   { width: 72px; }
.col-val.num { text-align: right; font-family: var(--mono); }
.col-note  { color: var(--p-surface-500); font-size: .65rem; }
</style>
