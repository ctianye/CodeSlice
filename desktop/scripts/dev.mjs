/**
 * Development runner: starts the Vite dev server for the renderer, waits for it
 * to accept connections, compiles the Electron main/preload TypeScript, then
 * launches Electron pointed at the dev server.
 *
 * The backend is NOT started here — run it separately (`uv run codeslice-backend`
 * in ../backend). Set CODESLICE_SESSION_TOKEN to the token it prints so the
 * renderer can authenticate.
 */
import { spawn } from 'node:child_process'
import { connect } from 'node:net'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const electronBinary = require('electron')

const here = dirname(fileURLToPath(import.meta.url))
const desktopRoot = resolve(here, '..')
const frontendRoot = resolve(desktopRoot, '..', 'frontend')
const DEV_URL = process.env.CODESLICE_DEV_URL ?? 'http://127.0.0.1:3000'
const DEV_PORT = Number(new URL(DEV_URL).port || '3000')
const npmCmd = process.platform === 'win32' ? 'npm.cmd' : 'npm'

const children = new Set()

function track(child, label) {
  children.add(child)
  child.on('exit', (code) => {
    children.delete(child)
    if (code !== 0 && code !== null) console.error(`[dev] ${label} 退出，代码 ${code}`)
  })
  child.on('error', (error) => console.error(`[dev] ${label} 启动失败：`, error))
  return child
}

function shutdown(code = 0) {
  for (const child of children) {
    try {
      child.kill()
    } catch {
      /* already gone */
    }
  }
  children.clear()
  process.exit(code)
}

process.on('SIGINT', () => shutdown(0))
process.on('SIGTERM', () => shutdown(0))

function waitForPort(port, host, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs
  return new Promise((resolvePromise, rejectPromise) => {
    const attempt = () => {
      const socket = connect({ port, host })
      socket.once('connect', () => {
        socket.destroy()
        resolvePromise()
      })
      socket.once('error', () => {
        socket.destroy()
        if (Date.now() > deadline) {
          rejectPromise(new Error(`等待 ${host}:${port} 超时`))
          return
        }
        setTimeout(attempt, 250)
      })
    }
    attempt()
  })
}

/** Probe the backend so a missing service is a clear warning, not a blank UI. */
function probeBackend() {
  return new Promise((resolvePromise) => {
    const socket = connect({ port: 8000, host: '127.0.0.1' })
    socket.once('connect', () => {
      socket.destroy()
      resolvePromise(true)
    })
    socket.once('error', () => {
      socket.destroy()
      resolvePromise(false)
    })
  })
}

async function main() {
  const backendUp = await probeBackend()
  if (!backendUp) {
    console.warn('[dev] 未检测到 http://127.0.0.1:8000 上的后端服务。')
    console.warn('[dev] 请先在 backend 目录启动：uv run codeslice-backend')
  }

  console.log('[dev] 正在启动 Vite 开发服务器…')
  track(
    spawn(npmCmd, ['run', 'dev', '--', '--host', '127.0.0.1'], {
      cwd: frontendRoot,
      stdio: 'inherit',
      shell: process.platform === 'win32',
    }),
    'vite',
  )
  await waitForPort(DEV_PORT, '127.0.0.1')

  console.log('[dev] 正在编译 Electron 主进程…')
  await new Promise((resolvePromise, rejectPromise) => {
    const tsc = track(
      spawn(npmCmd, ['run', 'build'], { cwd: desktopRoot, stdio: 'inherit', shell: process.platform === 'win32' }),
      'tsc',
    )
    tsc.on('exit', (code) => (code === 0 ? resolvePromise() : rejectPromise(new Error('Electron 编译失败'))))
  })

  console.log(`[dev] 启动 Electron（${DEV_URL}）…`)
  const electron = track(
    spawn(electronBinary, [desktopRoot], {
      cwd: desktopRoot,
      stdio: 'inherit',
      env: {
        ...process.env,
        CODESLICE_DEV: '1',
        CODESLICE_DEV_URL: DEV_URL,
      },
    }),
    'electron',
  )

  electron.on('exit', () => shutdown(0))
}

main().catch((error) => {
  console.error('[dev]', error instanceof Error ? error.message : error)
  shutdown(1)
})
