import { definePreset } from '@primeuix/themes'
import Aura from '@primeuix/themes/aura'

// OpenMagnetics-style teal accent (#539796) on a slate dark surface.
const teal = {
  50: '#f0faf9', 100: '#d6f0ee', 200: '#aee0dd', 300: '#7fccc8',
  400: '#54b3af', 500: '#539796', 600: '#3f7a79', 700: '#356363',
  800: '#2d5050', 900: '#274343', 950: '#132726',
}

export const OmAura = definePreset(Aura, {
  semantic: {
    primary: teal,
    colorScheme: {
      dark: {
        surface: {
          0: '#ffffff', 50: '#f7f8fa', 100: '#e9edf2', 200: '#cdd5df',
          300: '#9fadbf', 400: '#6b7c92', 500: '#475569', 600: '#33415a',
          700: '#26334a', 800: '#1b2640', 900: '#131c33', 950: '#0b1220',
        },
        primary: {
          color: '#54b3af', contrastColor: '#0b1220',
          hoverColor: '#7fccc8', activeColor: '#54b3af',
        },
        content: { background: '{surface.900}', borderColor: '{surface.800}' },
        text: { color: '{surface.100}', mutedColor: '{surface.400}' },
      },
    },
  },
})
