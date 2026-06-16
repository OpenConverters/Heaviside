<script setup>
// Graphical pipeline view for a job: stages as a horizontal flow with
// status colour, per-stage timing, and arrows showing the path.
import { computed } from 'vue'

const props = defineProps({
  stages: { type: Array, default: () => [] },
  status: { type: String, default: '' },   // overall job status
})

const fmtDur = (s) => {
  if (s == null) return ''
  if (s < 1) return `${Math.round(s * 1000)} ms`
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`
  const m = Math.floor(s / 60)
  return `${m}m ${Math.round(s % 60)}s`
}
const icon = (st) => ({
  done: 'pi pi-check', running: 'pi pi-spin pi-spinner',
  error: 'pi pi-times', pending: 'pi pi-circle',
}[st] || 'pi pi-circle')

const total = computed(() =>
  props.stages.reduce((a, s) => a + (s.duration_s || 0), 0))
</script>

<template>
  <div class="pf" v-if="stages.length">
    <div class="pf-track">
      <template v-for="(s, i) in stages" :key="i">
        <div class="pf-node" :class="`pf-${s.status}`" :title="`${s.name} — ${s.status}`">
          <div class="pf-dot"><i :class="icon(s.status)" /></div>
          <div class="pf-name">{{ s.name }}</div>
          <div class="pf-dur mono">{{ fmtDur(s.duration_s) || '—' }}</div>
        </div>
        <div v-if="i < stages.length - 1" class="pf-arrow"
             :class="{ lit: stages[i].status === 'done' }">
          <span class="pf-line" /><span class="pf-head">▶</span>
        </div>
      </template>
    </div>
    <div class="pf-foot mono">
      <span>{{ stages.filter(s => s.status === 'done').length }}/{{ stages.length }} stages</span>
      <span v-if="total > 0">· elapsed {{ fmtDur(total) }}</span>
    </div>
  </div>
  <div v-else class="muted mono pf-empty">no stage telemetry for this job</div>
</template>

<style scoped>
.pf { padding: .5rem .2rem .2rem; }
.pf-track {
  display: flex; align-items: stretch; flex-wrap: wrap; gap: .15rem;
}
.pf-node {
  display: flex; flex-direction: column; align-items: center; gap: .25rem;
  min-width: 96px; padding: .5rem .6rem; border-radius: 10px;
  border: 1px solid var(--grat-strong);
  background: linear-gradient(180deg, rgba(8,21,19,.6), rgba(6,16,15,.6));
  transition: border-color .3s, box-shadow .3s;
}
.pf-dot {
  width: 26px; height: 26px; border-radius: 50%;
  display: grid; place-items: center; font-size: .75rem;
  border: 1px solid var(--grat-strong); color: var(--p-surface-400);
}
.pf-name { font-family: var(--disp); font-size: .72rem; font-weight: 600;
  text-align: center; letter-spacing: .2px; color: var(--p-surface-200); }
.pf-dur { font-size: .62rem; color: var(--p-surface-400); }

/* states */
.pf-done .pf-dot   { color: var(--ch1); border-color: var(--ch1-deep);
  background: rgba(60,224,200,.12); }
.pf-done           { border-color: rgba(60,224,200,.35); }
.pf-running .pf-dot{ color: #06100f; background: var(--ch1); border-color: var(--ch1);
  box-shadow: 0 0 10px var(--ch1); }
.pf-running        { border-color: var(--ch1);
  box-shadow: 0 0 0 1px rgba(60,224,200,.35), 0 0 16px rgba(60,224,200,.18);
  animation: pf-pulse 1.4s ease-in-out infinite; }
.pf-running .pf-dur, .pf-running .pf-name { color: var(--ch1); }
.pf-error .pf-dot  { color: var(--fault); border-color: var(--fault);
  background: rgba(255,93,85,.14); }
.pf-error          { border-color: rgba(255,93,85,.5); }
.pf-pending        { opacity: .55; }

/* arrows */
.pf-arrow { display: flex; align-items: center; gap: 1px; min-width: 26px;
  color: var(--p-surface-600); }
.pf-arrow .pf-line { flex: 1; height: 2px; background: currentColor; border-radius: 2px; }
.pf-arrow .pf-head { font-size: .6rem; transform: translateX(-2px); }
.pf-arrow.lit { color: var(--ch1-deep); }

.pf-foot { margin-top: .55rem; display: flex; gap: .5rem; font-size: .65rem;
  color: var(--p-surface-400); }
.pf-empty { padding: .5rem; font-size: .72rem; }

@keyframes pf-pulse {
  0%,100% { box-shadow: 0 0 0 1px rgba(60,224,200,.3), 0 0 12px rgba(60,224,200,.12); }
  50%     { box-shadow: 0 0 0 1px rgba(60,224,200,.55), 0 0 22px rgba(60,224,200,.30); }
}
</style>
