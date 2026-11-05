import { contextBridge, ipcRenderer } from 'electron'

/**
 * Preload bridge. Exposes only what the renderer needs: the backend address,
 * the session token, a folder picker and an external-link opener.
 * Node/Electron objects are never handed to the page.
 */

interface BackendInfoPayload {
  apiBase: string
  token: string
  platform: string
}

function readBackendInfo(): BackendInfoPayload {
  try {
    const info = ipcRenderer.sendSync('codeslice:backend-info') as Partial<BackendInfoPayload> | undefined
    return {
      apiBase: typeof info?.apiBase === 'string' ? info.apiBase : '',
      token: typeof info?.token === 'string' ? info.token : '',
      platform: typeof info?.platform === 'string' ? info.platform : process.platform,
    }
  } catch {
    return { apiBase: '', token: '', platform: process.platform }
  }
}

const backend = readBackendInfo()

const bridge = {
  apiBase: backend.apiBase,
  token: backend.token,
  platform: backend.platform,
  /** Opens the native directory picker; resolves to null when cancelled. */
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('codeslice:pick-folder') as Promise<string | null>,
  /** Opens an http(s) URL in the system browser. */
  openExternal: (url: string): Promise<void> =>
    ipcRenderer.invoke('codeslice:open-external', url) as Promise<void>,
  minimize: (): Promise<void> => ipcRenderer.invoke('codeslice:window-minimize') as Promise<void>,
  toggleMaximize: (): Promise<boolean> => ipcRenderer.invoke('codeslice:window-toggle-maximize') as Promise<boolean>,
  closeWindow: (): Promise<void> => ipcRenderer.invoke('codeslice:window-close') as Promise<void>,
  isMaximized: (): boolean => ipcRenderer.sendSync('codeslice:window-state-subscribe') as boolean,
  onWindowState: (listener: (maximized: boolean) => void): (() => void) => {
    const handler = (_event: Electron.IpcRendererEvent, maximized: boolean) => listener(maximized)
    ipcRenderer.on('codeslice:window-state', handler)
    return () => ipcRenderer.removeListener('codeslice:window-state', handler)
  },
}

contextBridge.exposeInMainWorld('codeslice', bridge)

export type CodesliceBridge = typeof bridge
