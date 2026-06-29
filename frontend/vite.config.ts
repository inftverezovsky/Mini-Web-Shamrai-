import { defineConfig, type PluginOption } from 'vite'
import react from '@vitejs/plugin-react'
import { visualizer } from 'rollup-plugin-visualizer'

const browserTargets = ['chrome111', 'edge111', 'firefox114', 'safari16.4', 'ios16.4'];

function manualChunks(id: string) {
  const normalizedId = id.replace(/\\/g, '/');
  if (!normalizedId.includes('/node_modules/')) return undefined;

  if (/\/node_modules\/(react|react-dom|scheduler)\//.test(normalizedId)) {
    return 'react-vendor';
  }

  if (
    normalizedId.includes('/node_modules/react-router')
    || normalizedId.includes('/node_modules/@remix-run/router')
  ) {
    return 'router-vendor';
  }

  if (normalizedId.includes('/node_modules/@tanstack/')) {
    return 'query-vendor';
  }

  if (
    normalizedId.includes('/node_modules/framer-motion/')
    || normalizedId.includes('/node_modules/motion-dom/')
    || normalizedId.includes('/node_modules/motion-utils/')
    || normalizedId.includes('/node_modules/@motionone/')
  ) {
    return 'motion-vendor';
  }

  if (normalizedId.includes('/node_modules/lucide-react/')) {
    return 'icons-vendor';
  }

  if (normalizedId.includes('/node_modules/@vkontakte/vk-bridge/')) {
    return 'vk-vendor';
  }

  return 'vendor';
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const analyzeBundle = mode === 'analyze' || process.env.ANALYZE === 'true';
  const analyzerPlugin = analyzeBundle
    ? visualizer({
        filename: 'dist/stats.html',
        title: 'Shamrai Analytics Hub Bundle',
        template: 'treemap',
        gzipSize: true,
        brotliSize: true,
        open: false,
      }) as PluginOption
    : null;

  return {
    plugins: [
      react(),
      ...(analyzerPlugin ? [analyzerPlugin] : []),
    ],
    build: {
      target: browserTargets,
      cssTarget: browserTargets,
      sourcemap: analyzeBundle,
      rollupOptions: {
        output: {
          manualChunks,
        },
      },
    },
    server: {
      port: 5173,
      host: process.env.VITE_DEV_HOST || '127.0.0.1',
    },
  }
})
