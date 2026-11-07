import { spawn } from 'node:child_process'
import type { ChildProcess } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'

/**
 * Electron main process for CodeSlice.
 *
 * Responsibilities:
 *  - own the window lifecycle and remember its bounds between runs
 *  - start (production) or join (development) the local Python backend
 *  - hand the renderer a tiny bridge: { apiBase, token, pickFolder, openExternal, platform }
 *
 * The renderer never touches Node APIs; everything crosses `contextBridge`.
 */

interface BackendInfo {
  baseUrl: string
  token: string
}

interface WindowBounds {
  width: number
  height: number
  x?: number
  y?: number
  maximized?: boolean
}

const DEFAULT_DEV_PORT = 8000
const DEFAULT_WINDOW: WindowBounds = { width: 1360, height: 860 }
const READY_PREFIX = 'CODESLICE_READY'
const BACKEND_BOOT_TIMEOUT_MS = 30_000

let mainWindow: BrowserWindow | null = null
let backendProcess: ChildProcess | null = null
let backendInfo: BackendInfo | null = null
let quitting = false

const isDev = process.env.CODESLICE_DEV === '1' || !app.isPackaged

// ---------------------------------------------------------------------------
// Window bounds persistence
// ---------------------------------------------------------------------------

const boundsFile = (): string => join(app.getPath('userData'), 'window-state.json')

function loadBounds(): WindowBounds {
  try {
    const raw = readFileSync(boundsFile(), 'utf8')
    const parsed = JSON.parse(raw) as Partial<WindowBounds>
    if (typeof parsed.width === 'number' && typeof parsed.height === 'number') {
      return {
        width: Math.max(900, Math.round(parsed.width)),
        height: Math.max(600, Math.round(parsed.height)),
        x: typeof parsed.x === 'number' ? Math.round(parsed.x) : undefined,
        y: typeof parsed.y === 'number' ? Math.round(parsed.y) : undefined,
        maximized: Boolean(parsed.maximized),
      }
    }
  } catch {
    /* first run, or the file was removed — fall back to the default size */
  }
  return { ...DEFAULT_WINDOW }
}

function saveBounds(window: BrowserWindow): void {
  const maximized = window.isMaximized()
  // Keep the restored (non-maximized) size so un-maximizing looks right.
  const bounds = maximized ? window.getNormalBounds() : window.getBounds()
  const payload: WindowBounds = {
    width: bounds.width,
    height: bounds.height,
    x: bounds.x,
    y: bounds.y,
    maximized,
  }
  try {
    writeFileSync(boundsFile(), JSON.stringify(payload), 'utf8')
  } catch {
    /* a read-only profile directory is not worth crashing over */
  }
}

// ---------------------------------------------------------------------------
// Backend lifecycle
// ---------------------------------------------------------------------------

function backendExecutable(): string {
  return join(process.resourcesPath, 'backend', 'codeslice-backend.exe')
}

/**
 * The renderer bundle shipped for the backend to serve.
 *
 * Electron loads the UI from the backend's http origin instead of `file://`.
 * That matters: a `file://` page has the opaque origin `null`, and the API
 * requires a bearer token plus CORS, so every fetch would be rejected. Serving
 * the same bytes over http://127.0.0.1:<port> makes the renderer same-origin
 * with /api.
 */
function frontendDir(): string {
  return join(process.resourcesPath, 'frontend')
}

function hasBundledFrontend(): boolean {
  return existsSync(join(frontendDir(), 'index.html'))
}

/**
 * Development: the developer is expected to have started the backend already
 * (`uv run codeslice-backend`), so we only join it. The session token is taken
 * from the environment; the backend prints it on startup.
 */
function resolveDevBackend(): BackendInfo {
  const port = Number(process.env.CODESLICE_PORT ?? DEFAULT_DEV_PORT)
  const token = process.env.CODESLICE_SESSION_TOKEN ?? ''
  return { baseUrl: `http://127.0.0.1:${Number.isFinite(port) ? port : DEFAULT_DEV_PORT}`, token }
}

/**
 * Production: spawn the packaged backend with CODESLICE_PORT=0 (let the OS pick
 * a free port) and wait for it to print
 *   CODESLICE_READY {"port": 51234, "token": "..."}
 * on stdout.
 */
function startPackagedBackend(): Promise<BackendInfo> {
  const exe = backendExecutable()
  if (!existsSync(exe)) {
    return Promise.reject(new Error(`未找到后端可执行文件：${exe}`))
  }

  return new Promise<BackendInfo>((resolve, reject) => {
    const child = spawn(exe, [], {
      env: {
        ...process.env,
        CODESLICE_PORT: '0',
        CODESLICE_DATA_DIR: app.getPath('userData'),
        // Only set when we actually shipped the copy; otherwise the backend
        // falls back to its own discovery.
        ...(hasBundledFrontend() ? { CODESLICE_FRONTEND_DIST: frontendDir() } : {}),
      },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    backendProcess = child

    const stdout = child.stdout
    const stderrStream = child.stderr
    if (!stdout || !stderrStream) {
      stopBackend()
      reject(new Error('无法接管后端进程的输出流。'))
      return
    }

    let settled = false
    let buffer = ''
    let stderr = ''

    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      reject(new Error(`后端启动超时（${BACKEND_BOOT_TIMEOUT_MS / 1000} 秒）。${stderr ? `\n${stderr}` : ''}`))
    }, BACKEND_BOOT_TIMEOUT_MS)

    const finish = (error: Error | null, info?: BackendInfo) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      if (error) reject(error)
      else if (info) resolve(info)
    }

    stdout.setEncoding('utf8')
    stdout.on('data', (chunk: string) => {
      buffer += chunk
      let newline = buffer.indexOf('\n')
      while (newline >= 0) {
        const line = buffer.slice(0, newline).trim()
        buffer = buffer.slice(newline + 1)
        newline = buffer.indexOf('\n')
        if (!line.startsWith(READY_PREFIX)) continue
        const payload = line.slice(READY_PREFIX.length).trim()
        try {
          const parsed = JSON.parse(payload) as { port?: number; token?: string }
          if (typeof parsed.port === 'number' && typeof parsed.token === 'string') {
            finish(null, { baseUrl: `http://127.0.0.1:${parsed.port}`, token: parsed.token })
          }
        } catch {
          finish(new Error(`无法解析后端就绪信息：${line}`))
        }
      }
    })

    stderrStream.setEncoding('utf8')
    stderrStream.on('data', (chunk: string) => {
      stderr += chunk
      if (stderr.length > 8000) stderr = stderr.slice(-8000)
    })

    child.on('error', (error) => finish(error))
    child.on('exit', (code) => {
      backendProcess = null
      finish(new Error(`后端进程已退出（代码 ${code ?? '未知'}）。${stderr ? `\n${stderr}` : ''}`))
    })
  })
}

function stopBackend(): void {
  const child = backendProcess
  if (!child) return
  backendProcess = null
  try {
    child.kill()
  } catch {
    /* the process may already be gone */
  }
}

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------

async function createWindow(): Promise<void> {
  const bounds = loadBounds()

  mainWindow = new BrowserWindow({
    width: bounds.width,
    height: bounds.height,
    x: bounds.x,
    y: bounds.y,
    minWidth: 1000,
    minHeight: 640,
    frame: false,
    show: false,
    backgroundColor: '#f4f6f8',
    title: 'CodeSlice',
    icon: join(
      isDev ? app.getAppPath() : process.resourcesPath,
      isDev ? 'build-assets' : '',
      'icon.png',
    ),
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  })

  if (bounds.maximized) mainWindow.maximize()

  mainWindow.once('ready-to-show', () => mainWindow?.show())
  mainWindow.on('close', () => {
    if (mainWindow) saveBounds(mainWindow)
  })
  mainWindow.on('closed', () => {
    mainWindow = null
  })
  mainWindow.on('maximize', () => mainWindow?.webContents.send('codeslice:window-state', true))
  mainWindow.on('unmaximize', () => mainWindow?.webContents.send('codeslice:window-state', false))

  // External links open in the system browser, never inside the app shell.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })

  if (isDev) {
    const devUrl = process.env.CODESLICE_DEV_URL ?? 'http://127.0.0.1:3000'
    await mainWindow.loadURL(devUrl)
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else if (backendInfo && hasBundledFrontend()) {
    // Same origin as /api: no CORS preflight, no opaque `file://` origin.
    await mainWindow.loadURL(`${backendInfo.baseUrl}/`)
  } else {
    // Last resort (a build without the extraResources copy): the file:// page
    // can still render, but cross-origin API calls are up to the backend's CORS.
    await mainWindow.loadFile(join(__dirname, '..', '..', 'dist', 'index.html'))
  }
}

// ---------------------------------------------------------------------------
// IPC + bootstrap
// ---------------------------------------------------------------------------

function registerIpc(info: BackendInfo): void {
  ipcMain.handle('codeslice:window-minimize', () => mainWindow?.minimize())
  ipcMain.handle('codeslice:window-toggle-maximize', () => {
    if (!mainWindow) return false
    if (mainWindow.isMaximized()) mainWindow.unmaximize()
    else mainWindow.maximize()
    return mainWindow.isMaximized()
  })
  ipcMain.handle('codeslice:window-close', () => mainWindow?.close())
  ipcMain.handle('codeslice:window-is-maximized', () => mainWindow?.isMaximized() ?? false)

  ipcMain.on('codeslice:window-state-subscribe', (event) => {
    event.returnValue = mainWindow?.isMaximized() ?? false
  })

  ipcMain.handle('codeslice:pick-folder', async () => {
    const parent = mainWindow ?? undefined
    const result = parent
      ? await dialog.showOpenDialog(parent, { properties: ['openDirectory'], title: '选择代码项目目录' })
      : await dialog.showOpenDialog({ properties: ['openDirectory'], title: '选择代码项目目录' })
    if (result.canceled || result.filePaths.length === 0) return null
    return result.filePaths[0]
  })

  ipcMain.handle('codeslice:open-external', async (_event, url: unknown) => {
    if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) return
    await shell.openExternal(url)
  })

  // The preload script needs these synchronously to build window.codeslice
  // before the renderer's first script runs.
  ipcMain.on('codeslice:backend-info', (event) => {
    event.returnValue = {
      // In dev the renderer is served by Vite, which proxies /api to the
      // backend, so a same-origin base URL avoids cross-origin requests.
      apiBase: isDev ? '' : info.baseUrl,
      token: info.token,
      platform: process.platform,
    }
  })
}

async function bootstrap(): Promise<void> {
  backendInfo = isDev ? resolveDevBackend() : await startPackagedBackend()
  registerIpc(backendInfo)
  await createWindow()
}

// A single instance owns the backend process.
const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.focus()
  })

  app.whenReady().then(
    () => {
      void bootstrap().catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error)
        dialog.showErrorBox('CodeSlice 启动失败', message)
        app.quit()
      })

      app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) void createWindow()
      })
    },
    (error: unknown) => {
      const message = error instanceof Error ? error.message : String(error)
      dialog.showErrorBox('CodeSlice 启动失败', message)
      app.quit()
    },
  )

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
  })

  app.on('before-quit', () => {
    quitting = true
  })

  app.on('will-quit', () => {
    if (!quitting) return
    stopBackend()
  })
}
