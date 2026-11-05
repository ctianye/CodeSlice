/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Optional session token for browser-only development (see src/bridge.ts). */
  readonly VITE_SESSION_TOKEN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
