// Copies Monaco's AMD distribution into public/monaco so the editor never needs
// a CDN (Electron runs offline and the renderer CSP forbids remote scripts).
import { cp, mkdir, rm, stat } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')
const source = join(root, 'node_modules', 'monaco-editor', 'min', 'vs')
const target = join(root, 'public', 'monaco', 'vs')

const exists = async (p) => {
  try {
    await stat(p)
    return true
  } catch {
    return false
  }
}

if (!(await exists(source))) {
  console.warn('[copy-monaco] monaco-editor not installed yet, skipping copy')
  process.exit(0)
}

await rm(target, { recursive: true, force: true })
await mkdir(dirname(target), { recursive: true })
await cp(source, target, { recursive: true })
console.log('[copy-monaco] copied monaco assets to public/monaco/vs')
