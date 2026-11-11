/**
 * Bridge to the Electron preload script (see desktop/electron/preload.ts).
 * In a plain browser (vite dev without Electron) this is undefined and the
 * client falls back to the Vite proxy plus a manually entered token.
 */

export interface CodesliceBridge {
  apiBase: string
  token: string
  pickFolder: () => Promise<string | null>
  openExternal: (url: string) => Promise<void>
  platform: string
  arch: string
  minimize: () => Promise<void>
  toggleMaximize: () => Promise<boolean>
  closeWindow: () => Promise<void>
  isMaximized: () => boolean
  onWindowState: (listener: (maximized: boolean) => void) => () => void
}

declare global {
  interface Window {
    codeslice?: CodesliceBridge
  }
}

export const bridge: CodesliceBridge | undefined =
  typeof window !== 'undefined' ? window.codeslice : undefined

/** True when running inside the Electron shell. */
export const isDesktop = Boolean(bridge)

/** Base URL for API calls: the injected one, otherwise same-origin (Vite proxy). */
export const apiBase = (): string => {
  const base = bridge?.apiBase ?? ''
  return base.replace(/\/$/, '')
}

const TOKEN_STORAGE_KEY = 'codeslice.devToken'

/**
 * Session token resolution order:
 * 1. injected by Electron main (production + `desktop/npm run dev`)
 * 2. VITE_SESSION_TOKEN from frontend/.env.local (browser dev)
 * 3. value the developer pasted into the UI, kept in localStorage
 */
export function getToken(): string {
  if (bridge?.token) return bridge.token
  const fromEnv = (import.meta.env.VITE_SESSION_TOKEN as string | undefined) ?? ''
  if (fromEnv) return fromEnv
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setDevToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_STORAGE_KEY, token)
  } catch {
    /* storage may be disabled; the value still lives in the module caller */
  }
}

/**
 * True when a token is available from any source. Electron in dev mode has the
 * bridge but no token (the developer starts the backend manually), so the UI
 * still has to offer the manual entry field in that case.
 */
export const hasToken = (): boolean => getToken() !== ''

export function pickFolder(): Promise<string | null> {
  if (bridge) return bridge.pickFolder()
  return Promise.resolve(null)
}

export function openExternal(url: string): void {
  if (bridge) {
    void bridge.openExternal(url)
    return
  }
  window.open(url, '_blank', 'noopener,noreferrer')
}
