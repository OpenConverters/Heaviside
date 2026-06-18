<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import Designer from './views/Designer.vue'
import CrossReference from './views/CrossReference.vue'
import Jobs from './views/Jobs.vue'
import Catalog from './views/Catalog.vue'

const tab = ref('design')
const deepJob = ref(null)   // job id to auto-open from a #/jobs/<id> URL
const tabs = [
  { id: 'design', label: 'Converter Designer', icon: 'pi-cog' },
  { id: 'xref', label: 'Cross-Reference', icon: 'pi-sync' },
  { id: 'jobs', label: 'Jobs', icon: 'pi-server' },
  { id: 'catalog', label: 'TAS Catalog', icon: 'pi-database' },
]
const _ids = tabs.map((t) => t.id)

// Hash-based deep links (no vue-router): #/<tab> and #/jobs/<job_id>. Lets a
// result be bookmarked / shared with a fixed URL and survive a reload.
function applyHash() {
  const [section, id] = (location.hash || '').replace(/^#\/?/, '').split('/')
  if (_ids.includes(section)) tab.value = section
  deepJob.value = section === 'jobs' && id ? id : null
}
onMounted(() => {
  applyHash()
  window.addEventListener('hashchange', applyHash)
  // Self-hosted Umami (cookie-less) — separate website from openmagnetics.com
  const s = document.createElement('script')
  s.defer = true
  s.src = '/stats/script.js'
  s.setAttribute('data-website-id', '2e9c5afa-bf1f-41ee-949f-62fa9e0639f5')
  s.setAttribute('data-domains', 'heaviside.openconverters.com')
  document.head.appendChild(s)
})
onUnmounted(() => window.removeEventListener('hashchange', applyHash))
// Reflect tab switches in the hash (but don't clobber an active job deep link).
watch(tab, (t) => {
  if (!(t === 'jobs' && deepJob.value)) location.hash = `#/${t}`
})
</script>

<template>
  <header class="hv">
    <div class="hv-inner">
      <!-- Signature: the Heaviside unit-step response — rise, overshoot,
           ring-down, settle — sweeping across the scope graticule. -->
      <svg class="hv-trace" viewBox="0 0 1000 100" preserveAspectRatio="none"
           aria-hidden="true">
        <path d="M0,72 H300 C330,72 338,16 366,16 C394,16 402,48 432,44
                 C460,40 470,24 498,30 C524,35 536,32 566,34 H1000" />
      </svg>

      <div class="hv-brand">
        <h1>HEAVISIDE</h1>
        <span class="sub">power-converter bench</span>
      </div>

      <div class="hv-status">
        <span class="hv-ch c1"><b>CH1</b> auto-design</span>
        <span class="hv-ch c2"><b>CH2</b> cross-reference</span>
        <span class="hv-rec"><i></i> AI · online</span>
      </div>
    </div>
  </header>

  <div class="wrap">
    <nav class="nav">
      <button v-for="t in tabs" :key="t.id" :class="{ active: tab === t.id }"
              @click="tab = t.id">
        <i class="pi" :class="t.icon"></i>{{ t.label }}
      </button>
    </nav>

    <!-- v-show wrappers (single root) preserve in-progress state across tab
         switches; the view templates are multi-root so v-show can't sit on
         them directly. Jobs/Catalog use v-if to re-fetch fresh on each visit. -->
    <div v-show="tab === 'design'"><Designer /></div>
    <div v-show="tab === 'xref'"><CrossReference /></div>
    <Jobs v-if="tab === 'jobs'" :open-job="deepJob" />
    <Catalog v-if="tab === 'catalog'" />
  </div>
</template>
