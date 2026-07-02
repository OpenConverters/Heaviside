<script setup>
import { ref, watch, computed, onUnmounted } from 'vue'
import Tag from 'primevue/tag'
import { parseSheet, sectionVisible, fmtMTM, hasAny } from '../datasheetParsers.js'

const props = defineProps({
  visible: Boolean,
  category: String,
  mpn: String,
})
const emit = defineEmits(['update:visible'])
const close = () => emit('update:visible', false)

const loading = ref(false)
const error = ref(null)
const data = ref(null)

watch(() => [props.visible, props.category, props.mpn], async ([vis]) => {
  if (!vis || !props.mpn) return
  loading.value = true; error.value = null; data.value = null
  try {
    const r = await fetch(`/catalog/${props.category}/${encodeURIComponent(props.mpn)}/detail`)
    if (!r.ok) throw new Error(`${r.status}`)
    data.value = await r.json()
  } catch (e) { error.value = e?.message ?? String(e) }
  finally { loading.value = false }
}, { immediate: false })

const sheet = computed(() =>
  data.value ? parseSheet(data.value.category, data.value.data) : null)

// Close on Escape while the drawer is open.
function onKey(e) { if (e.key === 'Escape') close() }
watch(() => props.visible, (vis) => {
  if (vis) document.addEventListener('keydown', onKey)
  else document.removeEventListener('keydown', onKey)
})
onUnmounted(() => document.removeEventListener('keydown', onKey))
</script>

<template>
  <!-- Slide-over drawer -->
  <Teleport to="body">
    <Transition name="ds-slide">
      <div v-if="visible" class="ds-backdrop" @click.self="close">
        <div class="ds-drawer">
          <!-- Close button -->
          <button class="ds-close" aria-label="Close datasheet" @click="close">✕</button>

          <div v-if="loading" class="ds-loading">Loading…</div>
          <div v-else-if="error" class="ds-error">{{ error }}</div>

          <template v-else-if="sheet">
            <!-- ── Header ── -->
            <div class="ds-header">
              <div class="ds-mfr mono">{{ sheet.manufacturer }}</div>
              <div class="ds-mpn">{{ sheet.title }}</div>
              <div class="ds-tags">
                <Tag v-for="t in sheet.tags" :key="t" :value="t" severity="secondary" class="ds-tag" />
                <Tag v-if="sheet.status" :value="sheet.status"
                     :severity="sheet.status === 'production' ? 'success' : 'secondary'" class="ds-tag" />
              </div>
              <p v-if="sheet.description" class="ds-desc">{{ sheet.description }}</p>
              <a v-if="sheet.datasheetUrl" :href="sheet.datasheetUrl" target="_blank"
                 rel="noopener" class="ds-pdf-link mono">
                ↗ Manufacturer Datasheet
              </a>
            </div>

            <!-- ── Headline specs ── -->
            <div class="ds-headline">
              <div v-for="h in sheet.headline" :key="h.sym" class="ds-kv">
                <span class="ds-sym">{{ h.sym }}</span>
                <span class="ds-kv-val">{{ h.val }}</span>
                <span class="ds-kv-label">{{ h.label }}</span>
              </div>
            </div>

            <!-- ── Parameter sections ── -->
            <div v-for="sec in sheet.sections.filter(sectionVisible)" :key="sec.title" class="ds-section">
              <div class="ds-sec-title">{{ sec.title }}</div>
              <table class="ds-table">
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
          </template>

          <!-- Data arrived but no datasheet view exists for this category. -->
          <div v-else-if="data" class="ds-loading">
            No datasheet view for {{ props.category }} yet.
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* Drawer slide-over. Must stack above PrimeVue modals (mask z-index starts at
   1101 and increments per overlay) so MPN clicks inside the result-viewer
   dialog still surface the drawer. */
.ds-backdrop {
  position: fixed; inset: 0; z-index: 1500;
  background: rgba(0,0,0,.55);
  display: flex; justify-content: flex-end;
}
.ds-drawer {
  position: relative;
  width: min(680px, 95vw);
  height: 100vh;
  overflow-y: auto;
  background: var(--p-surface-900);
  border-left: 1px solid var(--p-surface-700);
  padding: 2rem 1.8rem 3rem;
  box-sizing: border-box;
}
.ds-close {
  position: absolute; top: 1rem; right: 1.2rem;
  background: none; border: none; color: var(--p-surface-400);
  font-size: 1rem; cursor: pointer; line-height: 1;
}
.ds-close:hover { color: var(--p-surface-100); }

/* Transition */
.ds-slide-enter-active, .ds-slide-leave-active { transition: transform .2s ease; }
.ds-slide-enter-from, .ds-slide-leave-to { transform: translateX(100%); }

/* Loading / error */
.ds-loading, .ds-error { color: var(--p-surface-400); font-size: .85rem; margin-top: 2rem; }
.ds-error { color: var(--fault, #f87171); }

/* Header */
.ds-header { margin-bottom: 1.4rem; }
.ds-mfr { font-size: .72rem; color: var(--p-surface-400); letter-spacing: .06em; text-transform: uppercase; margin-bottom: .2rem; }
.ds-mpn { font-size: 1.35rem; font-weight: 700; letter-spacing: .02em; color: var(--p-surface-50); line-height: 1.2; margin-bottom: .5rem; }
.ds-tags { display: flex; flex-wrap: wrap; gap: .3rem; margin-bottom: .6rem; }
.ds-tag { font-size: .65rem !important; }
.ds-desc { margin: 0 0 .6rem; font-size: .76rem; line-height: 1.5; color: var(--p-surface-300); }
.ds-pdf-link { font-size: .72rem; color: var(--ch1); text-decoration: none; }
.ds-pdf-link:hover { text-decoration: underline; }

/* Headline KV strip */
.ds-headline {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: .6rem;
  background: var(--p-surface-800);
  border: 1px solid var(--p-surface-700);
  border-radius: 6px;
  padding: .8rem 1rem;
  margin-bottom: 1.6rem;
}
.ds-kv { display: flex; flex-direction: column; gap: .1rem; }
.ds-sym { font-size: .68rem; color: var(--p-surface-400); font-family: monospace; }
.ds-kv-val { font-size: 1.05rem; font-weight: 700; color: var(--ch1); font-family: monospace; line-height: 1.1; }
.ds-kv-label { font-size: .62rem; color: var(--p-surface-500); }

/* Sections */
.ds-section { margin-bottom: 1.6rem; }
.ds-sec-title {
  font-size: .65rem; font-weight: 700; letter-spacing: .1em;
  text-transform: uppercase; color: var(--ch1);
  border-bottom: 1px solid var(--ch1-deep, #1a4a44);
  padding-bottom: .3rem; margin-bottom: .5rem;
}

/* Parameter table */
.ds-table {
  width: 100%; border-collapse: collapse;
  font-size: .73rem; line-height: 1.4;
}
.ds-table th {
  text-align: left; font-size: .6rem; font-weight: 600;
  letter-spacing: .07em; text-transform: uppercase;
  color: var(--p-surface-500);
  border-bottom: 1px solid var(--p-surface-700);
  padding: .2rem .4rem .3rem;
}
.ds-table td {
  padding: .25rem .4rem;
  border-bottom: 1px solid var(--p-surface-800);
  color: var(--p-surface-200);
  vertical-align: middle;
}
.ds-table tbody tr:hover td { background: var(--p-surface-800); }
.col-param { min-width: 160px; }
.col-sym { width: 70px; color: var(--p-surface-400); font-size: .68rem; }
.col-val { width: 72px; }
.col-val.num { text-align: right; font-family: monospace; }
.col-note { color: var(--p-surface-500); font-size: .65rem; }
.muted { color: var(--p-surface-500); }
</style>
