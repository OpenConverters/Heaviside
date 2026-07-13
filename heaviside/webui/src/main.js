import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import 'primeicons/primeicons.css'
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
