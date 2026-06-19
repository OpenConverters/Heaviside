import { ref } from 'vue'

// Shared singleton — any component can call openDatasheet() to show the drawer.
const visible = ref(false)
const mpn = ref(null)
const category = ref(null)

// Map loose component_type strings to catalog category keys.
const _TYPE_MAP = {
  mosfet: 'mosfets', mosfets: 'mosfets',
  igbt: 'mosfets', igbts: 'mosfets',
  diode: 'diodes', diodes: 'diodes',
  schottky: 'diodes', rectifier: 'diodes',
  capacitor: 'capacitors', capacitors: 'capacitors',
  cap: 'capacitors', electrolytic: 'capacitors', ceramic: 'capacitors',
  resistor: 'resistors', resistors: 'resistors',
  res: 'resistors', shunt: 'resistors',
  inductor: 'magnetics', magnetics: 'magnetics',
  transformer: 'magnetics', magnetic: 'magnetics',
  cmc: 'magnetics', choke: 'magnetics',
}

export function inferCategory(componentType) {
  if (!componentType) return null
  return _TYPE_MAP[componentType.toLowerCase().replace(/[^a-z]/g, '')] ?? null
}

export function useDatasheet() {
  function openDatasheet(mpnVal, categoryVal) {
    mpn.value = mpnVal
    category.value = categoryVal
    visible.value = true
  }
  return { visible, mpn, category, openDatasheet }
}
