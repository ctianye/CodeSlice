/**
 * Copies the built renderer (frontend/dist) into desktop/dist so the packaged
 * app and `electron .` can both load `dist/index.html` from here.
 * electron-builder packages desktop/dist as `files: [dist/**]`.
 */
import { cpSync, existsSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const desktopRoot = resolve(here, '..')
const source = resolve(desktopRoot, '..', 'frontend', 'dist')
const target = join(desktopRoot, 'dist')

if (!existsSync(source)) {
  console.error(`[copy-renderer] 未找到 ${source}，请先在 frontend 目录执行 npm run build`)
  process.exit(1)
}

rmSync(target, { recursive: true, force: true })
cpSync(source, target, { recursive: true })
console.log(`[copy-renderer] ${source} -> ${target}`)
