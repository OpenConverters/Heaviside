<script setup>
import { ref } from 'vue'
import Designer from './views/Designer.vue'
import CrossReference from './views/CrossReference.vue'
import Jobs from './views/Jobs.vue'
import Catalog from './views/Catalog.vue'

const tab = ref('design')
const tabs = [
  { id: 'design', label: 'Converter Designer', icon: 'pi-cog' },
  { id: 'xref', label: 'Cross-Reference', icon: 'pi-sync' },
  { id: 'jobs', label: 'Jobs', icon: 'pi-server' },
  { id: 'catalog', label: 'TAS Catalog', icon: 'pi-database' },
]
</script>

<template>
  <header class="hv">
    <div class="hv-inner">
      <span class="hv-mark">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
          <path d="M2 13 H6 L8 5 L12 19 L15 11 H22" stroke="#54b3af" stroke-width="1.8"
                stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </span>
      <h1>Heaviside <span class="sub">power-converter auto-design</span></h1>
      <span class="hv-status"><i></i> online · kimi-k2.5</span>
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
    <Jobs v-if="tab === 'jobs'" />
    <Catalog v-if="tab === 'catalog'" />
  </div>
</template>
