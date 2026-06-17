import { definePreset } from '@primeuix/themes'
import Aura from '@primeuix/themes/aura'

// "Bench instrument" theme — CH1 phosphor-aqua trace on a deep CRT teal-black
// enclosure. Dual-trace scope: amber (CH2) lives in CSS as the warning accent.
const phosphor = {
  50: '#e9fffb', 100: '#c5fdf2', 200: '#8df7e6', 300: '#52ecd6',
  400: '#3ce0c8', 500: '#1ec4ac', 600: '#129e8b', 700: '#147d70',
  800: '#16635a', 900: '#164f49', 950: '#06302c',
}

export const OmAura = definePreset(Aura, {
  semantic: {
    primary: phosphor,
    colorScheme: {
      dark: {
        surface: {
          0: '#ffffff', 50: '#eef6f4', 100: '#d8ece8', 200: '#b0d0ca',
          300: '#83aaa3', 400: '#5d8077', 500: '#3f5d56', 600: '#2a4640',
          700: '#1c3631', 800: '#142a26', 900: '#0c1d1a', 950: '#06100f',
        },
        primary: {
          color: '#3ce0c8', contrastColor: '#04201c',
          hoverColor: '#52ecd6', activeColor: '#3ce0c8',
        },
        content: { background: '{surface.900}', borderColor: '{surface.700}' },
        text: { color: '#d6e7e2', mutedColor: '{surface.400}' },
        formField: {
          background: '#091713', borderColor: '{surface.700}',
          focusBorderColor: '#3ce0c8',
        },
      },
    },
  },
})
