import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';
import { existsSync, symlinkSync } from 'node:fs';
const root = import.meta.dirname;
const upstream = resolve(root, 'vendor/drawnix');
if (!existsSync(resolve(upstream, 'node_modules'))) symlinkSync('../../node_modules', resolve(upstream, 'node_modules'), 'dir');
export default defineConfig({
  root, base: '/franchise-board/', plugins: [react()],
  resolve: { alias: {
    '@drawnix/drawnix': resolve(upstream, 'packages/drawnix/src/index.ts'),
    '@plait-board/react-board': resolve(upstream, 'packages/react-board/src/index.ts'),
    '@plait-board/react-text': resolve(upstream, 'packages/react-text/src/index.ts'),
  } },
  build: { outDir: resolve(root, '../../preview/h3-director/franchise-board'), emptyOutDir: true },
});
