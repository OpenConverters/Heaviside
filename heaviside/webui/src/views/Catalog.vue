<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import Select from 'primevue/select'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import CatalogDetail from './CatalogDetail.vue'
import CatalogOverview from './CatalogOverview.vue'
import { api } from '../api.js'

// View mode: 'overview' (show-off dashboard) | 'browse' (parametric search)
const mode = ref('overview')

// ── Per-category config ───────────────────────────────────────────────────────
// scale: multiply user input by this to get SI value sent to the API
// The API always stores/filters in SI (V, Ω, A, F, H).
const UNIT_CONFIG = {
  mosfets:    [{ label: 'Vds',     unit: 'V',  scale: 1     },
               { label: 'Rds(on)', unit: 'mΩ', scale: 1e-3  },
               { label: 'Id',      unit: 'A',  scale: 1     }],
  diodes:     [{ label: 'Vrrm',   unit: 'V',  scale: 1     },
               { label: 'If(avg)', unit: 'A',  scale: 1     },
               { label: 'Vf',      unit: 'V',  scale: 1     }],
  capacitors: [{ label: 'C',      unit: 'µF', scale: 1e-6  },
               { label: 'V',      unit: 'V',  scale: 1     },
               { label: 'ESR',    unit: 'mΩ', scale: 1e-3  }],
  resistors:  [{ label: 'R',      unit: 'Ω',  scale: 1     },
               { label: 'Tol',    unit: '%',  scale: 0.01  },
               { label: 'P',      unit: 'W',  scale: 1     }],
  magnetics:  [{ label: 'L',      unit: 'µH', scale: 1e-6  },
               { label: 'Isat',   unit: 'A',  scale: 1     },
               { label: 'DCR',    unit: 'mΩ', scale: 1e-3  }],
  connectors: [{ label: 'V',         unit: 'V',   scale: 1 },
               { label: 'I/contact', unit: 'A',   scale: 1 },
               { label: 'Pos',       unit: 'pos', scale: 1 }],
}

// Friendly labels for tech filter chips per category
const TECH_LABELS = {
  mosfets:    { Si: 'Silicon', SiC: 'SiC', GaN: 'GaN' },
  diodes:     { schottky: 'Schottky', sicSchottky: 'SiC Schottky', ultrafast: 'Ultrafast',
                rectifier: 'Rectifier', standard: 'Standard', zener: 'Zener', fastRecovery: 'Fast Recovery' },
  capacitors: {
    'ceramic-class-1': 'Ceramic C0G/NP0',
    'ceramic-class-2': 'Ceramic X5R/X7R',
    'ceramic-class-3': 'Ceramic Y5V',
    'aluminum-electrolytic-wet': 'Electrolytic',
    'aluminum-electrolytic-polymer': 'Polymer',
    'aluminum-hybrid-polymer': 'Hybrid Polymer',
    'film-polypropylene': 'Film PP',
    'film-polyester': 'Film PET',
    'film-paper': 'Film Paper',
    'film-polyphenylene-sulfide': 'Film PPS',
    'tantalum-mno2': 'Tantalum MnO₂',
    'tantalum-polymer': 'Tantalum Polymer',
    'tantalum-wet': 'Tantalum Wet',
    'supercapacitor-edlc': 'Supercapacitor',
    'thin-film-silicon': 'Thin Film',
  },
  resistors:  {},
  magnetics:  { inductor: 'Inductor', chipBead: 'Chip Bead', commonModeChoke: 'CMC',
                transformer: 'Transformer', coupledInductor: 'Coupled Inductor' },
  connectors: {
    boardToBoard: 'Board-to-Board', dataInterface: 'Data Interface',
    pinHeaderSocket: 'Pin Header/Socket', wireToBoard: 'Wire-to-Board',
    circular: 'Circular', terminalBlock: 'Terminal Block', fpcFfc: 'FPC/FFC',
    cardEdge: 'Card Edge', power: 'Power', rf: 'RF',
  },
}

const categories = ['mosfets', 'diodes', 'capacitors', 'resistors', 'magnetics', 'connectors']
const category = ref('mosfets')

// ── Filter state ──────────────────────────────────────────────────────────────
const q = ref('')
const selectedTech = ref('')       // single tech value or ''
const p1Min = ref('')
const p1Max = ref('')
const p2Min = ref('')
const p2Max = ref('')
const p3Min = ref('')
const p3Max = ref('')
const sortBy = ref('')             // p1|p2|p3|mpn|mfr|''
const sortOrder = ref('asc')      // asc|desc

// ── Data state ────────────────────────────────────────────────────────────────
const rows = ref([])
const total = ref(0)              // filtered total before limit
const offset = ref(0)
const PAGE = 50

const stats = ref(null)           // { counts: {...}, total: N }
const facets = ref(null)          // { total, techs, p1, p2, p3 }
const loading = ref(false)
const loadingMore = ref(false)
const loadingFacets = ref(false)
const error = ref(null)

// ── Detail view ───────────────────────────────────────────────────────────────
const detailMpn = ref(null)
const detailCategory = ref(null)

// ── Computed helpers ──────────────────────────────────────────────────────────
const unitCfg = computed(() => UNIT_CONFIG[category.value] || [])
const techLabels = computed(() => TECH_LABELS[category.value] || {})
const availableTechs = computed(() => facets.value?.techs?.filter(t => t) ?? [])
const hasFilters = computed(() =>
  q.value || selectedTech.value || p1Min.value || p1Max.value ||
  p2Min.value || p2Max.value || p3Min.value || p3Max.value
)

// Range input helpers — lets the template bind to indexed refs cleanly
const _pMins = [p1Min, p2Min, p3Min]
const _pMaxs = [p1Max, p2Max, p3Max]
function getRMin(i) { return _pMins[i].value }
function getRMax(i) { return _pMaxs[i].value }
function setRMin(i, v) { _pMins[i].value = v; load() }
function setRMax(i, v) { _pMaxs[i].value = v; load() }

const PARAM_KEYS = ['p1', 'p2', 'p3']

function siVal(raw, scale) {
  const n = parseFloat(raw)
  return isNaN(n) ? null : n * scale
}

function buildOpts() {
  const cfg = unitCfg.value
  return {
    q: q.value,
    limit: PAGE,
    offset: offset.value,
    tech: selectedTech.value,
    sort: sortBy.value,
    order: sortOrder.value,
    p1Min: siVal(p1Min.value, cfg[0]?.scale ?? 1),
    p1Max: siVal(p1Max.value, cfg[0]?.scale ?? 1),
    p2Min: siVal(p2Min.value, cfg[1]?.scale ?? 1),
    p2Max: siVal(p2Max.value, cfg[1]?.scale ?? 1),
    p3Min: siVal(p3Min.value, cfg[2]?.scale ?? 1),
    p3Max: siVal(p3Max.value, cfg[2]?.scale ?? 1),
  }
}

// Placeholder showing DB range in display units
function rangePlaceholder(facetKey, idx) {
  if (!facets.value) return null
  const range = facets.value[facetKey]
  const scale = unitCfg.value[idx]?.scale ?? 1
  if (!range?.min && !range?.max) return null
  const fmt = v => v != null ? +((v / scale).toPrecision(3)) : '?'
  return `${fmt(range.min)} – ${fmt(range.max)}`
}

// ── Load functions ────────────────────────────────────────────────────────────
async function load() {
  offset.value = 0
  rows.value = []
  loading.value = true; error.value = null
  try {
    const j = await api.catalog(category.value, buildOpts())
    rows.value = j.rows
    total.value = j.total
  } catch (e) { error.value = e?.message ?? String(e) }
  finally { loading.value = false }
}

async function loadMore() {
  offset.value += PAGE
  loadingMore.value = true
  try {
    const j = await api.catalog(category.value, buildOpts())
    rows.value.push(...j.rows)
    total.value = j.total
  } catch (e) { error.value = e?.message ?? String(e) }
  finally { loadingMore.value = false }
}

async function loadFacets() {
  facets.value = null; loadingFacets.value = true
  try { facets.value = await api.catalogFacets(category.value) }
  catch { /* non-fatal */ }
  finally { loadingFacets.value = false }
}

// ── Debounced search ──────────────────────────────────────────────────────────
let _debTimer = null
function scheduleLoad() {
  clearTimeout(_debTimer)
  _debTimer = setTimeout(load, 280)
}

// ── Clear all filters ─────────────────────────────────────────────────────────
function clearAll() {
  q.value = ''; selectedTech.value = ''
  p1Min.value = p1Max.value = p2Min.value = p2Max.value = p3Min.value = p3Max.value = ''
  sortBy.value = ''; sortOrder.value = 'asc'
  load()
}

// ── Sorting ───────────────────────────────────────────────────────────────────
function setSort(field) {
  if (sortBy.value === field) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortBy.value = field
    sortOrder.value = 'asc'
  }
  load()
}

function sortIcon(field) {
  if (sortBy.value !== field) return 'pi pi-sort'
  return sortOrder.value === 'asc' ? 'pi pi-sort-amount-up-alt' : 'pi pi-sort-amount-down-alt'
}

// ── Category change ───────────────────────────────────────────────────────────
function changeCategory() {
  selectedTech.value = ''
  p1Min.value = p1Max.value = p2Min.value = p2Max.value = p3Min.value = p3Max.value = ''
  sortBy.value = ''; sortOrder.value = 'asc'
  loadFacets()
  load()
}

// ── Overview → Browse jump ────────────────────────────────────────────────────
function pickCategory(cat) {
  if (categories.includes(cat)) category.value = cat
  mode.value = 'browse'
  changeCategory()
}

// ── Detail navigation ─────────────────────────────────────────────────────────
function cardHref(row) {
  return `#/catalog/${category.value}/${encodeURIComponent(row.mpn)}`
}

function openDetail(row, event) {
  event?.preventDefault()
  location.hash = `#/catalog/${category.value}/${encodeURIComponent(row.mpn)}`
}

function parseHash() {
  const parts = (location.hash || '').replace(/^#\/?/, '').split('/')
  if (parts[0] === 'catalog' && parts[1] && parts[2]) {
    detailCategory.value = parts[1]
    detailMpn.value = decodeURIComponent(parts[2])
    if (categories.includes(parts[1])) category.value = parts[1]
  } else {
    detailMpn.value = null
    detailCategory.value = null
  }
}

function goBack() { location.hash = '#/catalog' }

// ── Format helper for stat numbers ───────────────────────────────────────────
function fmtN(n) {
  if (n == null) return '—'
  return n.toLocaleString()
}

onMounted(async () => {
  window.addEventListener('hashchange', parseHash)
  parseHash()
  // Load stats once (global, cached server-side after first call)
  api.catalogStats().then(s => { stats.value = s }).catch(() => {})
  await loadFacets()
  load()
})
onUnmounted(() => window.removeEventListener('hashchange', parseHash))
</script>

<template>
  <CatalogDetail v-if="detailMpn"
    :mpn="detailMpn" :category="detailCategory"
    @close="goBack" />

  <div v-else>
    <!-- ── Mode toggle: Overview dashboard vs parametric Browse ─────────── -->
    <div class="cat-mode">
      <button class="cat-mode-btn" :class="{ active: mode === 'overview' }"
              @click="mode = 'overview'">
        <i class="pi pi-chart-bar" /> Overview
      </button>
      <button class="cat-mode-btn" :class="{ active: mode === 'browse' }"
              @click="mode = 'browse'">
        <i class="pi pi-search" /> Browse
      </button>
    </div>

    <CatalogOverview v-if="mode === 'overview'" @pick="pickCategory" />

    <template v-else>
    <!-- ── DB Stats banner ──────────────────────────────────────────────── -->
    <div class="db-banner">
      <div class="db-banner-label mono">◈ INTERNAL COMPONENT DB</div>
      <div class="db-cells">
        <div v-for="cat in categories" :key="cat" class="db-cell"
             :class="{ active: cat === category }"
             @click="category = cat; changeCategory()">
          <span class="db-cell-n mono">{{ stats ? fmtN(stats.counts[cat]) : '…' }}</span>
          <span class="db-cell-label">{{ cat }}</span>
        </div>
        <div class="db-cell db-total">
          <span class="db-cell-n mono">{{ stats ? fmtN(stats.total) : '…' }}</span>
          <span class="db-cell-label">total</span>
        </div>
      </div>
    </div>

    <div class="panel">
      <!-- ── Category + search row ──────────────────────────────────────── -->
      <div class="filter-top">
        <div class="field" style="min-width:140px">
          <label class="fld-label">Category</label>
          <Select v-model="category" :options="categories" @change="changeCategory" />
        </div>
        <div class="field" style="flex:1">
          <label class="fld-label">Search MPN or manufacturer</label>
          <InputText v-model="q" style="width:100%" placeholder="e.g. Infineon, IRF, 744…"
                     @input="scheduleLoad" @keyup.enter="load" />
        </div>
        <div style="align-self:flex-end">
          <Button label="Search" icon="pi pi-search" size="small" :loading="loading" @click="load" />
        </div>
      </div>

      <!-- ── Tech chips ─────────────────────────────────────────────────── -->
      <div v-if="availableTechs.length" class="filter-row">
        <span class="filter-row-label mono">Tech</span>
        <div class="tech-chips">
          <button v-for="t in availableTechs" :key="t"
                  class="tech-chip" :class="{ sel: selectedTech === t }"
                  @click="selectedTech = selectedTech === t ? '' : t; load()">
            {{ techLabels[t] || t }}
          </button>
          <button v-if="selectedTech" class="tech-chip tech-chip-clear" @click="selectedTech = ''; load()">
            × clear
          </button>
        </div>
      </div>

      <!-- ── Parametric range filters ───────────────────────────────────── -->
      <div class="filter-ranges">
        <div v-for="(cfg, i) in unitCfg" :key="cfg.label" class="range-group">
          <span class="range-label mono">{{ cfg.label }}</span>
          <input class="range-input" type="number" step="any"
                 :placeholder="rangePlaceholder(PARAM_KEYS[i], i)?.split(' – ')[0] ?? 'min'"
                 :value="getRMin(i)"
                 @change="setRMin(i, $event.target.value)" />
          <span class="range-sep">–</span>
          <input class="range-input" type="number" step="any"
                 :placeholder="rangePlaceholder(PARAM_KEYS[i], i)?.split(' – ')[1] ?? 'max'"
                 :value="getRMax(i)"
                 @change="setRMax(i, $event.target.value)" />
          <span class="range-unit muted mono">{{ cfg.unit }}</span>
        </div>

        <!-- Sort controls inline -->
        <div class="sort-group">
          <span class="range-label mono">Sort</span>
          <button v-for="(cfg, i) in unitCfg" :key="cfg.label"
                  class="sort-btn" :class="{ active: sortBy === PARAM_KEYS[i] }"
                  @click="setSort(PARAM_KEYS[i])">
            {{ cfg.label }}
            <i class="pi" :class="sortIcon(PARAM_KEYS[i])" style="font-size:.65rem"></i>
          </button>
          <button class="sort-btn" :class="{ active: sortBy === 'mpn' }" @click="setSort('mpn')">
            MPN <i class="pi" :class="sortIcon('mpn')" style="font-size:.65rem"></i>
          </button>
          <button v-if="hasFilters || sortBy" class="sort-btn sort-btn-clear" @click="clearAll">
            × clear all
          </button>
        </div>
      </div>

      <!-- ── Results header ─────────────────────────────────────────────── -->
      <div class="results-bar">
        <span v-if="!loading" class="stage-line">
          <template v-if="hasFilters || sortBy">
            <b style="color:var(--ch1)">{{ fmtN(total) }}</b> matching ·
            {{ fmtN(rows.length) }} shown
            <span class="muted"> · {{ fmtN(facets?.total) }} in DB</span>
          </template>
          <template v-else>
            <b style="color:var(--ch1)">{{ fmtN(facets?.total ?? total) }}</b>
            {{ category }} in DB · showing {{ rows.length }}
          </template>
        </span>
        <span v-else class="stage-line muted">Loading…</span>
      </div>

      <!-- ── Card grid ──────────────────────────────────────────────────── -->
      <div class="cat-grid">
        <a v-for="row in rows" :key="row.mpn"
           :href="cardHref(row)"
           class="cat-card"
           @click="openDetail(row, $event)">
          <div class="cat-card-head">
            <span class="cat-mpn mono">{{ row.mpn }}</span>
            <!-- "unknown" = the vendor's parametric data had no lifecycle
                 field; show no claim rather than a word that reads like one. -->
            <Tag v-if="row.status && row.status !== 'unknown'"
                 :severity="row.status === 'production' ? 'success' : 'secondary'"
                 :value="row.status" class="cat-status" />
          </div>
          <div class="cat-mfr">
            {{ row.manufacturer }}<span v-if="row.tech" class="cat-tech"> · {{ techLabels[row.tech] || row.tech }}</span>
          </div>
          <div v-if="row.p1 || row.p2 || row.p3" class="cat-specs">
            <div v-if="row.p1" class="cat-kv">
              <span class="cat-kv-label">{{ unitCfg[0]?.label }}</span>
              <span class="cat-kv-val mono">{{ row.p1 }}</span>
            </div>
            <div v-if="row.p2" class="cat-kv">
              <span class="cat-kv-label">{{ unitCfg[1]?.label }}</span>
              <span class="cat-kv-val mono">{{ row.p2 }}</span>
            </div>
            <div v-if="row.p3" class="cat-kv">
              <span class="cat-kv-label">{{ unitCfg[2]?.label }}</span>
              <span class="cat-kv-val mono">{{ row.p3 }}</span>
            </div>
          </div>
        </a>
      </div>

      <div v-if="!loading && !rows.length && !error" class="cat-empty stage-line">
        No results — try relaxing the filters.
      </div>

      <!-- ── Load more ──────────────────────────────────────────────────── -->
      <div v-if="rows.length < total" class="load-more-row">
        <Button label="Load more" icon="pi pi-chevron-down" size="small"
                severity="secondary" outlined :loading="loadingMore"
                @click="loadMore" />
        <span class="stage-line muted">{{ fmtN(rows.length) }} of {{ fmtN(total) }}</span>
      </div>

      <Message v-if="error" severity="error" style="margin-top:.6rem">{{ error }}</Message>
    </div>
    </template>
  </div>
</template>

<style scoped>
/* ── Mode toggle ──────────────────────────────────────────────────────────── */
.cat-mode {
  display: inline-flex;
  gap: .25rem;
  padding: .25rem;
  border: 1px solid var(--p-surface-700);
  border-radius: 8px;
  background: var(--p-surface-900);
  margin-bottom: .9rem;
}
.cat-mode-btn {
  display: inline-flex; align-items: center; gap: .4rem;
  font-family: var(--mono);
  font-size: .72rem; font-weight: 600; letter-spacing: .04em;
  padding: .38rem .9rem;
  border: none; border-radius: 6px;
  background: none; color: var(--p-surface-400);
  cursor: pointer; transition: background .12s, color .12s;
}
.cat-mode-btn:hover { color: var(--p-surface-100); }
.cat-mode-btn.active {
  background: rgba(60,224,200,.12);
  color: var(--ch1);
  box-shadow: inset 0 0 0 1px rgba(60,224,200,.4);
}

/* ── DB stats banner ──────────────────────────────────────────────────────── */
.db-banner {
  background: linear-gradient(135deg,
    color-mix(in srgb, var(--p-surface-900) 90%, var(--ch1) 10%),
    var(--p-surface-900));
  border: 1px solid var(--p-surface-700);
  border-bottom: none;
  border-radius: 8px 8px 0 0;
  padding: .75rem 1.2rem .65rem;
}
.db-banner-label {
  font-size: .6rem;
  letter-spacing: .14em;
  text-transform: uppercase;
  color: var(--ch1);
  margin-bottom: .5rem;
  opacity: .85;
}
.db-cells {
  display: flex;
  gap: .5rem;
  flex-wrap: wrap;
  align-items: center;
}
.db-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: .06rem;
  padding: .35rem .75rem;
  border: 1px solid var(--p-surface-700);
  border-radius: 5px;
  background: var(--p-surface-850, color-mix(in srgb, var(--p-surface-800) 50%, var(--p-surface-900)));
  cursor: pointer;
  transition: border-color .12s, background .12s;
  min-width: 80px;
}
.db-cell:hover { border-color: rgba(60,224,200,.4); }
.db-cell.active {
  border-color: rgba(60,224,200,.6);
  background: rgba(60,224,200,.08);
}
.db-cell-n {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--p-surface-50);
  line-height: 1.1;
  letter-spacing: .03em;
}
.db-cell.active .db-cell-n { color: var(--ch1); }
.db-cell-label {
  font-size: .58rem;
  color: var(--p-surface-500);
  text-transform: uppercase;
  letter-spacing: .08em;
}
.db-total {
  border-color: rgba(60,224,200,.25);
  background: rgba(60,224,200,.04);
  margin-left: .5rem;
}
.db-total .db-cell-n { color: var(--ch1); font-size: 1.2rem; }
.db-total .db-cell-label { color: var(--ch1); opacity: .6; }

/* ── Panel (sits below banner, no top radius) ─────────────────────────────── */
.panel { border-radius: 0 0 8px 8px; }

/* ── Filter rows ──────────────────────────────────────────────────────────── */
.filter-top {
  display: flex;
  gap: .75rem;
  align-items: flex-end;
  flex-wrap: wrap;
  margin-bottom: .8rem;
}
.filter-row {
  display: flex;
  align-items: center;
  gap: .6rem;
  flex-wrap: wrap;
  margin-bottom: .7rem;
}
.filter-row-label {
  font-size: .6rem;
  text-transform: uppercase;
  letter-spacing: .1em;
  color: var(--p-surface-500);
  min-width: 32px;
}

/* ── Tech chips ───────────────────────────────────────────────────────────── */
.tech-chips { display: flex; flex-wrap: wrap; gap: .35rem; }
.tech-chip {
  font-family: var(--mono);
  font-size: .72rem;
  font-weight: 500;
  padding: .28rem .65rem;
  border-radius: 4px;
  border: 1px solid var(--p-surface-700);
  background: var(--p-surface-800);
  color: var(--p-surface-300);
  cursor: pointer;
  transition: all .12s;
}
.tech-chip:hover { border-color: rgba(60,224,200,.4); color: var(--p-surface-100); }
.tech-chip.sel {
  background: rgba(60,224,200,.12);
  border-color: rgba(60,224,200,.6);
  color: var(--ch1);
  font-weight: 700;
}
.tech-chip-clear { color: var(--p-surface-500); border-style: dashed; }

/* ── Range filters ────────────────────────────────────────────────────────── */
.filter-ranges {
  display: flex;
  flex-wrap: wrap;
  gap: .6rem 1.2rem;
  align-items: center;
  padding: .65rem .8rem;
  background: var(--p-surface-850, color-mix(in srgb, var(--p-surface-800) 50%, var(--p-surface-900)));
  border: 1px solid var(--p-surface-700);
  border-radius: 6px;
  margin-bottom: .8rem;
}
.range-group {
  display: flex;
  align-items: center;
  gap: .3rem;
}
.range-label {
  font-size: .62rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .07em;
  color: var(--p-surface-400);
  min-width: 44px;
  text-align: right;
}
.range-input {
  width: 72px;
  background: var(--p-surface-900);
  border: 1px solid var(--p-surface-600);
  border-radius: 4px;
  color: var(--p-surface-100);
  font-family: var(--mono);
  font-size: .75rem;
  padding: .28rem .4rem;
  outline: none;
  transition: border-color .12s;
  -moz-appearance: textfield;
}
.range-input::-webkit-outer-spin-button,
.range-input::-webkit-inner-spin-button { -webkit-appearance: none; }
.range-input:focus { border-color: rgba(60,224,200,.55); }
.range-sep { color: var(--p-surface-600); font-size: .8rem; }
.range-unit { font-size: .62rem; }

/* ── Sort controls ────────────────────────────────────────────────────────── */
.sort-group {
  display: flex;
  align-items: center;
  gap: .3rem;
  margin-left: auto;
}
.sort-btn {
  font-family: var(--mono);
  font-size: .68rem;
  font-weight: 500;
  padding: .28rem .55rem;
  border-radius: 4px;
  border: 1px solid var(--p-surface-700);
  background: var(--p-surface-800);
  color: var(--p-surface-400);
  cursor: pointer;
  transition: all .12s;
  display: inline-flex;
  align-items: center;
  gap: .3rem;
}
.sort-btn:hover { border-color: rgba(60,224,200,.4); color: var(--p-surface-200); }
.sort-btn.active {
  background: rgba(60,224,200,.1);
  border-color: rgba(60,224,200,.5);
  color: var(--ch1);
}
.sort-btn-clear { color: var(--p-surface-500); border-style: dashed; margin-left: .4rem; }

/* ── Results bar ──────────────────────────────────────────────────────────── */
.results-bar {
  display: flex;
  align-items: center;
  gap: .6rem;
  margin-bottom: .7rem;
  min-height: 1.4rem;
}

/* ── Card grid ────────────────────────────────────────────────────────────── */
.cat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: .6rem;
}
.cat-card {
  display: flex;
  flex-direction: column;
  gap: .28rem;
  background: var(--p-surface-800);
  border: 1px solid var(--p-surface-700);
  border-radius: 6px;
  padding: .78rem .88rem .7rem;
  text-decoration: none;
  color: inherit;
  cursor: pointer;
  transition: border-color .13s, background .13s;
  min-width: 0;
  outline: none;
}
.cat-card:hover {
  border-color: rgba(60,224,200,.5);
  background: color-mix(in srgb, var(--p-surface-800) 70%, var(--p-surface-700));
}
.cat-card:active { opacity: .8; }

.cat-card-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: .35rem;
}
.cat-mpn { font-size: .86rem; font-weight: 700; color: var(--p-surface-50);
  letter-spacing: .02em; line-height: 1.25; word-break: break-all; min-width: 0; }
.cat-status { font-size: .58rem !important; flex-shrink: 0; margin-top: 2px; }
.cat-mfr { font-size: .7rem; color: var(--p-surface-400);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cat-tech { color: var(--p-surface-500); }

.cat-specs {
  display: flex; gap: .65rem; flex-wrap: wrap;
  margin-top: .35rem; padding-top: .42rem;
  border-top: 1px solid var(--p-surface-700);
}
.cat-kv { display: flex; flex-direction: column; gap: .05rem; min-width: 44px; }
.cat-kv-label { font-size: .56rem; font-weight: 600; color: var(--p-surface-500);
  text-transform: uppercase; letter-spacing: .07em; }
.cat-kv-val { font-size: .8rem; font-weight: 600; color: var(--ch1); line-height: 1.15; }

/* ── Load more ────────────────────────────────────────────────────────────── */
.load-more-row {
  display: flex;
  align-items: center;
  gap: .8rem;
  margin-top: .9rem;
  padding-top: .8rem;
  border-top: 1px solid var(--p-surface-800);
}

.cat-empty { padding: 2.5rem 0; text-align: center; }
</style>
