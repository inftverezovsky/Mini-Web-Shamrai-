/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        slate: {
          350: '#CBD5E1',
          450: '#7C8DA3',
          550: '#5F6F85',
          650: '#405066',
          750: '#263449',
        },
        cyan: {
          50: '#E0F7FC',
          100: '#B3EDFA',
          200: '#80E0F7',
          300: 'rgb(var(--color-primary-rgb) / <alpha-value>)',
          400: 'rgb(var(--color-primary-rgb) / <alpha-value>)', // Electric Blue base
          500: 'rgb(var(--color-primary-rgb) / <alpha-value>)',
          600: '#009BC0',
          700: '#007CA0',
          800: '#005F80',
          900: '#004260',
          950: '#00253D',
        },
        pink: {
          50: '#FFE0F0',
          100: '#FFB3D6',
          200: '#FF80BD',
          300: '#FF4DA3',
          400: 'rgb(var(--color-secondary-rgb) / <alpha-value>)',
          450: 'rgb(var(--color-secondary-rgb) / <alpha-value>)',
          500: 'rgb(var(--color-secondary-rgb) / <alpha-value>)', // Neon Pink base
          600: '#D6006B',
          700: '#AD0056',
          800: '#850042',
          900: '#5C002D',
          950: '#3D001F',
        },
        rose: {
          50: '#FFE0F0',
          100: '#FFB3D6',
          200: '#FF80BD',
          300: '#FF4DA3',
          400: 'rgb(var(--color-secondary-rgb) / <alpha-value>)',
          450: 'rgb(var(--color-secondary-rgb) / <alpha-value>)', // preserve old 450 key for compatibility
          500: 'rgb(var(--color-secondary-rgb) / <alpha-value>)', // Neon Pink base
          600: '#D6006B',
          700: '#AD0056',
          800: '#850042',
          900: '#5C002D',
          950: '#3D001F',
        },
        emerald: {
          450: '#20C997',
        },
        // Premium Dark Theme Palette
        dark: {
          bg: '#030612',       // deeper dark slate-950-like bg
          card: '#0a0f1d',     // slate-950 card border/fill
          border: '#1E293B',   // slate-800
          text: '#F8FAFC',     // slate-50
          muted: '#94A3B8'     // slate-400
        },
        // Neon Sport/Betting Accents
        sport: {
          green: '#10B981',    // emerald-500
          greenHover: '#059669',
          lightGreen: '#34D399',
          red: '#EF4444',      // rose-500
          redHover: '#DC2626',
          gold: '#F59E0B',     // amber-500
          blue: '#3B82F6'      // blue-500
        }
      },
      fontFamily: {
        sans: ['"Segoe UI Variable Text"', '"Segoe UI"', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        display: ['"Segoe UI Variable Display"', '"Segoe UI"', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        brand: ['"Spicy Rice"', 'system-ui', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'sans-serif'],
        mono: ['"Cascadia Mono"', '"SFMono-Regular"', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
      spacing: {
        '0.2': '0.05rem',
        '4.5': '1.125rem',
        '6.5': '1.625rem',
      },
      zIndex: {
        45: '45',
      },
      boxShadow: {
        'neon-green': '0 0 15px rgba(16, 185, 129, 0.25)',
        'neon-red': '0 0 15px rgba(239, 68, 68, 0.25)',
        'neon-rose': '0 0 15px rgba(255, 0, 127, 0.35)',
        'neon-indigo': '0 0 15px rgba(99, 102, 241, 0.28)',
        'neon-amber': '0 0 15px rgba(245, 158, 11, 0.28)',
        'neon-teal': '0 0 15px rgba(20, 184, 166, 0.28)',
        'neon-cyan': '0 0 15px rgba(0, 210, 255, 0.35)',
        'glass': '0 8px 32px 0 rgba(0, 0, 0, 0.37)'
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: 0, transform: 'translateY(6px)' },
          '100%': { opacity: 1, transform: 'translateY(0)' },
        },
        slideDown: {
          '0%': { opacity: 0, transform: 'translateY(-6px)' },
          '100%': { opacity: 1, transform: 'translateY(0)' },
        },
        scaleUp: {
          '0%': { opacity: 0, transform: 'scale(0.96)' },
          '100%': { opacity: 1, transform: 'scale(1)' },
        },
        spinSlow: {
          '100%': { transform: 'rotate(360deg)' },
        },
      },
      animation: {
        'fade-in': 'fadeIn 0.28s ease-out both',
        'slide-down': 'slideDown 0.22s ease-out both',
        'scale-up': 'scaleUp 0.24s ease-out both',
        'spin-slow': 'spinSlow 12s linear infinite',
      }
    },
  },
  plugins: [],
}
