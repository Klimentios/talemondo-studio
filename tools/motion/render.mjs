// Renders one motion graphic with alpha (ProRes 4444) for the edit sheet: node render.mjs <request.json>.
// request: { template, props, width, height, fps, durationInFrames, output }. The Remotion bundle is cached in
// .bundle/<hash of src>, so only the first render after a template change pays for bundling.
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, renameSync, rmSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bundle } from '@remotion/bundler';
import { renderMedia, selectComposition } from '@remotion/renderer';

const here = dirname(fileURLToPath(import.meta.url));
// Remotion's downloaded headless shell never loads the bundle on this Mac; the installed Google Chrome does.
// It runs with its own throwaway profile. STUDIO_CHROME overrides the path.
const CHROME = process.env.STUDIO_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const browser = existsSync(CHROME) ? { browserExecutable: CHROME } : {};
const src = join(here, 'src');

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  }).sort();
}

async function serveUrl() {
  const hash = createHash('sha256');
  for (const file of files(src)) hash.update(file.slice(src.length)).update(readFileSync(file));
  const target = join(here, '.bundle', hash.digest('hex').slice(0, 16));
  if (existsSync(join(target, 'index.html'))) return target;
  const temp = `${target}.tmp-${process.pid}`;
  await bundle({ entryPoint: join(src, 'index.ts'), outDir: temp, enableCaching: true });
  rmSync(target, { recursive: true, force: true });
  renameSync(temp, target);
  return target;
}

const request = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const inputProps = { ...request.props, width: request.width, height: request.height, fps: request.fps,
  durationInFrames: request.durationInFrames };
const url = await serveUrl();
const composition = await selectComposition({ serveUrl: url, id: request.template, inputProps, ...browser });
const temp = `${request.output}.part.mov`;
await renderMedia({ composition, serveUrl: url, codec: 'prores', proResProfile: '4444', pixelFormat: 'yuva444p10le',
  imageFormat: 'png', outputLocation: temp, inputProps, muted: true, overwrite: true, logLevel: 'error', ...browser });
renameSync(temp, request.output);
console.log(JSON.stringify({ output: request.output, frames: composition.durationInFrames }));
