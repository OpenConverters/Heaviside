import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import 'primeicons/primeicons.css'
import './style.css'
import { OmAura } from './theme.js'
import App from './App.vue'

createApp(App)
  .use(createPinia())
  .use(PrimeVue, {
    theme: { preset: OmAura, options: { darkModeSelector: '.om-dark' } },
  })
  .mount('#app')
