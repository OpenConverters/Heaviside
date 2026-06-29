<script setup>
import { ref, computed, onMounted } from 'vue'
import Message from 'primevue/message'
import { api } from '../api.js'

const emit = defineEmits(['pick'])

// ── Per-category presentation ────────────────────────────────────────────────
const CAT_META = {
  mosfets:    { label: 'MOSFETs',    accent: '#3ce0c8', glyph: '⎍' },
  diodes:     { label: 'Diodes',     accent: '#ffb84d', glyph: '▷|' },
  capacitors: { label: 'Capacitors', accent: '#5fa8ff', glyph: '‖' },
  resistors:  { label: 'Resistors',  accent: '#ff6b8a', glyph: '⊟' },
  magnetics:  { label: 'Magnetics',  accent: '#b07cff', glyph: '◠' },
  connectors: { label: 'Connectors', accent: '#6ee7a8', glyph: '▦' },
}
const ORDER = ['mosfets', 'diodes', 'capacitors', 'resistors', 'magnetics', 'connectors']

// Friendly tech names (matches the browse view); falls back to a humanizer.
const TECH_LABELS = {
  Si: 'Silicon', SiC: 'SiC', GaN: 'GaN',
  schottky: 'Schottky', sicSchottky: 'SiC Schottky', ultrafast: 'Ultrafast',
  rectifier: 'Rectifier', standard: 'Standard', zener: 'Zener', fastRecovery: 'Fast Recovery',
  'ceramic-class-1': 'Ceramic C0G', 'ceramic-class-2': 'Ceramic X7R', 'ceramic-class-3': 'Ceramic Y5V',
  'aluminum-electrolytic-wet': 'Al Electrolytic', 'aluminum-electrolytic-polymer': 'Al Polymer',
  'aluminum-hybrid-polymer': 'Al Hybrid', 'film-polypropylene': 'Film PP',
  'film-polyester': 'Film PET', 'film-paper': 'Film Paper', 'film-polyphenylene-sulfide': 'Film PPS',
  'tantalum-mno2': 'Tantalum MnO₂', 'tantalum-polymer': 'Tantalum Poly', 'tantalum-wet': 'Tantalum Wet',
  'supercapacitor-edlc': 'Supercap', 'thin-film-silicon': 'Thin Film',
  inductor: 'Inductor', transformer: 'Transformer', chipBead: 'Chip Bead', commonModeChoke: 'CMC',
  boardToBoard: 'Board-to-Board', dataInterface: 'Data Interface', pinHeaderSocket: 'Pin Header/Socket',
  wireToBoard: 'Wire-to-Board', circular: 'Circular', terminalBlock: 'Terminal Block',
  fpcFfc: 'FPC/FFC', cardEdge: 'Card Edge', power: 'Power',
}
function techName(t) {
  return TECH_LABELS[t] || t.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

// ── Data ─────────────────────────────────────────────────────────────────────
const data = ref(null)
const loading = ref(true)
const error = ref(null)

const cats = computed(() => {
  if (!data.value) return []
  return ORDER
    .filter(k => data.value.categories[k])
    .map(k => ({ key: k, ...CAT_META[k], ...data.value.categories[k] }))
})

// ── Animated count-up for the hero number ────────────────────────────────────
const heroN = ref(0)
function countUp(target) {
  const dur = 1100
  const start = performance.now()
  const step = (now) => {
    const t = Math.min(1, (now - start) / dur)
    const eased = 1 - Math.pow(1 - t, 3)          // easeOutCubic
    heroN.value = Math.round(target * eased)
    if (t < 1) requestAnimationFrame(step)
    else heroN.value = target
  }
  requestAnimationFrame(step)
}

const fmt = n => (n == null ? '—' : n.toLocaleString())
function prodPct(c) { return c.count ? Math.round((c.production / c.count) * 100) : 0 }
function sharePct(c) { return data.value?.total ? (c.count / data.value.total) * 100 : 0 }

// A short list of distribution segments + a "+N more" remainder.
function techSegments(c, max = 5) {
  const total = c.techs.reduce((s, t) => s + t.count, 0) || 1
  const head = c.techs.slice(0, max)
  const restCount = c.techs.slice(max).reduce((s, t) => s + t.count, 0)
  const segs = head.map(t => ({ name: techName(t.name), count: t.count, pct: (t.count / total) * 100 }))
  if (restCount > 0) segs.push({ name: `+${c.techs.length - max} more`, count: restCount, pct: (restCount / total) * 100, rest: true })
  return segs
}
function mfrBars(c) {
  const top = c.manufacturers[0]?.count || 1
  return c.manufacturers.slice(0, 5).map(m => ({ ...m, w: (m.count / top) * 100 }))
}

onMounted(async () => {
  try {
    data.value = await api.catalogOverview()
    countUp(data.value.total)
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="ov">
    <!-- ── Hero ─────────────────────────────────────────────────────────── -->
    <div class="ov-hero">
      <div class="ov-hero-bg" aria-hidden="true">
        <svg viewBox="0 0 1200 160" preserveAspectRatio="none">
          <path d="M0,120 H260 C290,120 300,40 330,40 C360,40 372,96 402,92
                   C440,88 452,30 500,40 C546,50 560,118 610,118 H1200" />
        </svg>
      </div>
      <div class="ov-hero-main">
        <div class="ov-hero-label mono">◈ INTERNAL COMPONENT DATABASE</div>
        <div class="ov-hero-n mono">{{ fmt(heroN) }}</div>
        <div class="ov-hero-sub">real, datasheet-backed parts ready to design with</div>
      </div>
      <div v-if="data" class="ov-hero-meta">
        <div class="ov-meta-cell">
          <span class="ov-meta-n mono">{{ cats.length }}</span>
          <span class="ov-meta-l">categories</span>
        </div>
        <div class="ov-meta-cell">
          <span class="ov-meta-n mono">{{ fmt(data.manufacturerTotal) }}</span>
          <span class="ov-meta-l">manufacturers</span>
        </div>
        <div class="ov-meta-cell">
          <span class="ov-meta-n mono">{{ Math.round(
            cats.reduce((s,c)=>s+c.production,0) / Math.max(1,cats.reduce((s,c)=>s+c.count,0)) * 100) }}%</span>
          <span class="ov-meta-l">in production</span>
        </div>
      </div>
    </div>

    <!-- ── Category share bar ───────────────────────────────────────────── -->
    <div v-if="data" class="ov-share">
      <div class="ov-share-bar">
        <div v-for="c in cats" :key="c.key" class="ov-share-seg"
             :style="{ width: sharePct(c) + '%', background: c.accent }"
             :title="`${c.label}: ${fmt(c.count)} (${sharePct(c).toFixed(1)}%)`"
             @click="emit('pick', c.key)" />
      </div>
      <div class="ov-share-legend">
        <button v-for="c in cats" :key="c.key" class="ov-legend-item" @click="emit('pick', c.key)">
          <span class="ov-dot" :style="{ background: c.accent }" />
          {{ c.label }} <span class="muted mono">{{ sharePct(c).toFixed(0) }}%</span>
        </button>
      </div>
    </div>

    <!-- ── Loading / error ──────────────────────────────────────────────── -->
    <div v-if="loading" class="ov-loading mono">⟳ aggregating component database…</div>
    <Message v-if="error" severity="error">{{ error }}</Message>

    <!-- ── Category cards ───────────────────────────────────────────────── -->
    <div class="ov-grid">
      <div v-for="c in cats" :key="c.key" class="ov-card"
           :style="{ '--accent': c.accent }" @click="emit('pick', c.key)">
        <div class="ov-card-glow" aria-hidden="true" />

        <div class="ov-card-head">
          <div class="ov-card-title">
            <span class="ov-glyph mono" :style="{ color: c.accent }">{{ c.glyph }}</span>
            <span class="ov-card-name">{{ c.label }}</span>
          </div>
          <i class="pi pi-arrow-right ov-card-go" />
        </div>

        <div class="ov-card-n mono">{{ fmt(c.count) }}</div>
        <div class="ov-card-tags">
          <span class="ov-tag" :style="{ borderColor: c.accent, color: c.accent }">
            {{ prodPct(c) }}% production
          </span>
          <span class="ov-tag ov-tag-muted">{{ c.manufacturerCount }} makers</span>
          <span class="ov-tag ov-tag-muted">{{ sharePct(c).toFixed(0) }}% of DB</span>
        </div>

        <!-- Technology distribution -->
        <div v-if="c.techs.length" class="ov-block">
          <div class="ov-block-label mono">TECHNOLOGY MIX</div>
          <div class="ov-distbar">
            <div v-for="(s, i) in techSegments(c)" :key="i" class="ov-distseg"
                 :class="{ rest: s.rest }"
                 :style="{ width: s.pct + '%', background: s.rest ? 'var(--p-surface-600)'
                   : `color-mix(in srgb, ${c.accent} ${100 - i * 14}%, var(--p-surface-700))` }"
                 :title="`${s.name}: ${fmt(s.count)}`" />
          </div>
          <div class="ov-distlist">
            <span v-for="(s, i) in techSegments(c)" :key="i" class="ov-distitem">
              <span class="ov-distdot" :style="{ background: s.rest ? 'var(--p-surface-600)'
                : `color-mix(in srgb, ${c.accent} ${100 - i * 14}%, var(--p-surface-700))` }" />
              {{ s.name }}<span class="muted mono"> {{ fmt(s.count) }}</span>
            </span>
          </div>
        </div>

        <!-- Top manufacturers -->
        <div class="ov-block">
          <div class="ov-block-label mono">TOP MANUFACTURERS</div>
          <div class="ov-mfrs">
            <div v-for="m in mfrBars(c)" :key="m.name" class="ov-mfr">
              <span class="ov-mfr-name">{{ m.name }}</span>
              <span class="ov-mfr-track">
                <span class="ov-mfr-fill" :style="{ width: m.w + '%', background: c.accent }" />
              </span>
              <span class="ov-mfr-n mono">{{ fmt(m.count) }}</span>
            </div>
          </div>
        </div>

        <!-- Parameter coverage -->
        <div class="ov-block ov-cover">
          <div class="ov-block-label mono">PARAMETER COVERAGE</div>
          <div v-for="p in c.params" :key="p.label" class="ov-cover-row">
            <span class="ov-cover-k mono">{{ p.label }}</span>
            <span class="ov-cover-v mono" v-if="p.minFmt">{{ p.minFmt }} <span class="muted">→</span> {{ p.maxFmt }}</span>
            <span class="ov-cover-v muted mono" v-else>—</span>
          </div>
        </div>

        <div class="ov-card-cta mono">BROWSE {{ c.label.toUpperCase() }} →</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ov { display: flex; flex-direction: column; gap: 1rem; }

/* ── Hero ──────────────────────────────────────────────────────────────────── */
.ov-hero {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 1.5rem;
  padding: 1.8rem 2rem;
  border: 1px solid var(--p-surface-700);
  border-radius: 10px;
  background:
    radial-gradient(120% 140% at 0% 0%, rgba(60,224,200,.10), transparent 55%),
    linear-gradient(135deg, var(--p-surface-900), var(--p-surface-950, var(--p-surface-900)));
}
.ov-hero-bg { position: absolute; inset: 0; opacity: .5; pointer-events: none; }
.ov-hero-bg svg { width: 100%; height: 100%; }
.ov-hero-bg path { fill: none; stroke: var(--ch1); stroke-width: 1.5;
  opacity: .35; filter: drop-shadow(0 0 6px var(--ch1)); }
.ov-hero-main { position: relative; z-index: 1; }
.ov-hero-label {
  font-size: .62rem; letter-spacing: .22em; text-transform: uppercase;
  color: var(--ch1); opacity: .9; margin-bottom: .3rem;
}
.ov-hero-n {
  font-size: 3.6rem; font-weight: 800; line-height: 1;
  letter-spacing: .01em;
  color: var(--p-surface-0, #fff);
  text-shadow: 0 0 24px rgba(60,224,200,.45);
  font-variant-numeric: tabular-nums;
}
.ov-hero-sub { margin-top: .45rem; font-size: .82rem; color: var(--p-surface-400); }
.ov-hero-meta { position: relative; z-index: 1; display: flex; gap: 1.4rem; flex-wrap: wrap; }
.ov-meta-cell { display: flex; flex-direction: column; align-items: flex-end; }
.ov-meta-n { font-size: 1.5rem; font-weight: 700; color: var(--ch1); line-height: 1.1; }
.ov-meta-l { font-size: .6rem; text-transform: uppercase; letter-spacing: .1em;
  color: var(--p-surface-500); }

/* ── Share bar ─────────────────────────────────────────────────────────────── */
.ov-share {
  border: 1px solid var(--p-surface-700); border-radius: 8px;
  padding: .85rem 1rem; background: var(--p-surface-900);
}
.ov-share-bar {
  display: flex; height: 14px; border-radius: 7px; overflow: hidden;
  background: var(--p-surface-800); gap: 2px;
}
.ov-share-seg { cursor: pointer; transition: filter .12s, transform .12s; min-width: 3px; }
.ov-share-seg:hover { filter: brightness(1.25); }
.ov-share-legend { display: flex; flex-wrap: wrap; gap: .3rem 1rem; margin-top: .6rem; }
.ov-legend-item {
  display: inline-flex; align-items: center; gap: .4rem;
  background: none; border: none; cursor: pointer; padding: 0;
  font-size: .72rem; color: var(--p-surface-300);
}
.ov-legend-item:hover { color: var(--p-surface-50); }
.ov-dot { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }

.ov-loading { padding: 2rem 0; text-align: center; color: var(--ch1); opacity: .8;
  letter-spacing: .08em; font-size: .82rem; }

/* ── Card grid ─────────────────────────────────────────────────────────────── */
.ov-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: .9rem;
}
.ov-card {
  position: relative;
  overflow: hidden;
  display: flex; flex-direction: column; gap: .7rem;
  padding: 1.1rem 1.15rem 1rem;
  border: 1px solid var(--p-surface-700);
  border-radius: 9px;
  background: var(--p-surface-900);
  cursor: pointer;
  transition: border-color .15s, transform .15s, box-shadow .15s;
}
.ov-card:hover {
  border-color: color-mix(in srgb, var(--accent) 55%, transparent);
  transform: translateY(-2px);
  box-shadow: 0 8px 26px -12px color-mix(in srgb, var(--accent) 50%, transparent);
}
.ov-card-glow {
  position: absolute; top: -40%; right: -30%; width: 220px; height: 220px;
  border-radius: 50%; pointer-events: none;
  background: radial-gradient(circle, color-mix(in srgb, var(--accent) 16%, transparent), transparent 70%);
}
.ov-card-head { display: flex; align-items: center; justify-content: space-between; }
.ov-card-title { display: flex; align-items: center; gap: .55rem; }
.ov-glyph { font-size: 1.05rem; }
.ov-card-name { font-size: 1rem; font-weight: 700; color: var(--p-surface-50);
  letter-spacing: .02em; }
.ov-card-go { color: var(--p-surface-600); font-size: .8rem; transition: color .15s, transform .15s; }
.ov-card:hover .ov-card-go { color: var(--accent); transform: translateX(3px); }

.ov-card-n {
  font-size: 2.1rem; font-weight: 800; line-height: 1;
  color: var(--p-surface-0, #fff); letter-spacing: .01em;
  font-variant-numeric: tabular-nums;
}
.ov-card-tags { display: flex; flex-wrap: wrap; gap: .35rem; }
.ov-tag {
  font-size: .6rem; font-weight: 600; letter-spacing: .04em;
  padding: .15rem .45rem; border-radius: 4px;
  border: 1px solid var(--accent); color: var(--accent);
  background: color-mix(in srgb, var(--accent) 8%, transparent);
}
.ov-tag-muted { border-color: var(--p-surface-700); color: var(--p-surface-400);
  background: none; }

.ov-block { display: flex; flex-direction: column; gap: .4rem; }
.ov-block-label { font-size: .56rem; letter-spacing: .14em; color: var(--p-surface-500); }

/* tech distribution */
.ov-distbar { display: flex; height: 9px; border-radius: 5px; overflow: hidden;
  background: var(--p-surface-800); gap: 1.5px; }
.ov-distseg { min-width: 2px; transition: filter .12s; }
.ov-distseg:hover { filter: brightness(1.3); }
.ov-distlist { display: flex; flex-wrap: wrap; gap: .2rem .7rem; }
.ov-distitem { display: inline-flex; align-items: center; gap: .3rem;
  font-size: .67rem; color: var(--p-surface-300); }
.ov-distdot { width: 7px; height: 7px; border-radius: 2px; }

/* manufacturers */
.ov-mfrs { display: flex; flex-direction: column; gap: .28rem; }
.ov-mfr { display: grid; grid-template-columns: 88px 1fr 46px; align-items: center; gap: .5rem; }
.ov-mfr-name { font-size: .7rem; color: var(--p-surface-300);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ov-mfr-track { height: 6px; border-radius: 3px; background: var(--p-surface-800); overflow: hidden; }
.ov-mfr-fill { display: block; height: 100%; border-radius: 3px; opacity: .85; }
.ov-mfr-n { font-size: .68rem; color: var(--p-surface-400); text-align: right; }

/* parameter coverage */
.ov-cover-row { display: grid; grid-template-columns: 56px 1fr; align-items: baseline; gap: .5rem; }
.ov-cover-k { font-size: .66rem; font-weight: 600; color: var(--p-surface-400);
  text-transform: uppercase; letter-spacing: .04em; }
.ov-cover-v { font-size: .74rem; color: var(--p-surface-100); }

.ov-card-cta {
  margin-top: .15rem; font-size: .6rem; letter-spacing: .12em;
  color: var(--accent); opacity: 0; transition: opacity .15s;
}
.ov-card:hover .ov-card-cta { opacity: .85; }
</style>
