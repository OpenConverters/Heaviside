import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import 'primeicons/primeicons.css'
// Self-hosted fonts (was Google Fonts CDN — see security assessment N1). Only the
// weights the UI actually uses are bundled; Vite fingerprints + serves them same-origin.
import '@fontsource/chakra-petch/400.css'
import '@fontsource/chakra-petch/500.css'
import '@fontsource/chakra-petch/600.css'
import '@fontsource/chakra-petch/700.css'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/600.css'
import '@fontsource/jetbrains-mono/700.css'
import './style.css'
import { OmAura } from './theme.js'
import App from './App.vue'
import { initTelemetry, trackEvent } from './telemetry.js'

// Interaction telemetry → the shared /telemetry pipeline (openconverters_telemetry
// schema). Umami itself is already injected by App.vue on the prod host with the
// registered website-id, so we pass umamiWebsiteId:null here to avoid loading it
// twice; trackEvent still mirrors events to that window.umami. Server-side JOB
// telemetry (heaviside_telemetry) is separate and untouched.
initTelemetry({ site: 'heaviside', umamiWebsiteId: null })
trackEvent('app_open')

createApp(App)
  .use(createPinia())
  .use(PrimeVue, {
    theme: { preset: OmAura, options: { darkModeSelector: '.om-dark' } },
  })
  .use(ConfirmationService)
  .mount('#app')
