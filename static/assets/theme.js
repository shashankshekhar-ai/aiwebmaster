// Shared Tailwind config — one design-token source across all pages.
// Loaded before the Tailwind CDN script, which reads window.tailwind.config.
window.tailwind = window.tailwind || {};
window.tailwind.config = {
  theme: {
    extend: {
      colors: {
        brand: {
          navy: '#0a2135',
          navy2: '#0d2c46',
          panel: '#122f4a',
          panel2: '#173a56',
          border: '#20415e',
          gold: '#f5b93f',
          teal: '#3fa39a',
          terracotta: '#c9835a',
          ink: '#eef3f7',
          muted: '#8ba3b6',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
      },
      boxShadow: {
        panel: '0 1px 2px rgba(0,0,0,.2), 0 0 0 1px rgba(255,255,255,.02)',
        lift: '0 8px 24px -8px rgba(0,0,0,.45), 0 1px 2px rgba(0,0,0,.3), 0 0 0 1px rgba(255,255,255,.03)',
        glow: '0 0 0 3px rgba(245,185,63,.15)',
        'glow-teal': '0 0 0 3px rgba(63,163,154,.15)',
        'glow-terracotta': '0 0 0 3px rgba(201,131,90,.15)',
      },
      keyframes: {
        fadeIn: { '0%': { opacity: 0 }, '100%': { opacity: 1 } },
        fadeInUp: { '0%': { opacity: 0, transform: 'translateY(6px)' }, '100%': { opacity: 1, transform: 'translateY(0)' } },
        fadeInLeft: { '0%': { opacity: 0, transform: 'translateX(-8px)' }, '100%': { opacity: 1, transform: 'translateX(0)' } },
        popIn: { '0%': { opacity: 0, transform: 'scale(.96)' }, '100%': { opacity: 1, transform: 'scale(1)' } },
        shimmer: { '0%': { backgroundPosition: '-400px 0' }, '100%': { backgroundPosition: '400px 0' } },
        pulseDot: { '0%,100%': { opacity: 1 }, '50%': { opacity: .35 } },
      },
      animation: {
        'fade-in': 'fadeIn .3s ease-out both',
        'fade-in-up': 'fadeInUp .35s cubic-bezier(.16,1,.3,1) both',
        'fade-in-left': 'fadeInLeft .3s cubic-bezier(.16,1,.3,1) both',
        'pop-in': 'popIn .2s cubic-bezier(.16,1,.3,1) both',
        shimmer: 'shimmer 1.6s linear infinite',
        'pulse-dot': 'pulseDot 1.8s ease-in-out infinite',
      },
    },
  },
};
