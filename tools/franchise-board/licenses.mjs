// Preserve notices for bundled open-source packages without an extra build dependency.
import { readdirSync, readFileSync, existsSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
const root = import.meta.dirname;
let notices = readFileSync(resolve(root, 'vendor/drawnix/UPSTREAM.md'), 'utf8') + '\n' + readFileSync(resolve(root, 'vendor/drawnix/LICENSE'), 'utf8');
function collect(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name.startsWith('.')) continue;
    const folder = resolve(dir, entry.name);
    if (entry.name.startsWith('@')) { collect(folder); continue; }
    const metadata = resolve(folder, 'package.json');
    if (!existsSync(metadata)) continue;
    const pkg = JSON.parse(readFileSync(metadata, 'utf8'));
    notices += `\n\n--- ${pkg.name} ${pkg.version} (${JSON.stringify(pkg.license || 'see package')}) ---\n`;
    const license = readdirSync(folder).find(f => /^licen[cs]e(?:\.|$)/i.test(f));
    if (license) { try { notices += readFileSync(resolve(folder, license), 'utf8'); } catch {} }
    if (existsSync(resolve(folder, 'node_modules'))) collect(resolve(folder, 'node_modules'));
  }
}
collect(resolve(root, 'node_modules'));
writeFileSync(resolve(root, '../../preview/h3-director/franchise-board/THIRD_PARTY_LICENSES.txt'), notices);
