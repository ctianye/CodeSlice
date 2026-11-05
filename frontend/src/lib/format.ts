/** Formats an ISO8601 UTC timestamp as local Chinese time. */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** "3 分钟前" style relative time, falling back to absolute past a week. */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const diff = Date.now() - date.getTime()
  const minute = 60_000
  if (diff < minute) return '刚刚'
  if (diff < 60 * minute) return `${Math.floor(diff / minute)} 分钟前`
  if (diff < 24 * 60 * minute) return `${Math.floor(diff / (60 * minute))} 小时前`
  if (diff < 7 * 24 * 60 * minute) return `${Math.floor(diff / (24 * 60 * minute))} 天前`
  return formatTime(iso)
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(2)} MB`
}

/** Shortens a Windows path for display: keeps the tail, ellipsises the head. */
export function shortenPath(path: string, keep = 42): string {
  if (path.length <= keep) return path
  return `…${path.slice(path.length - keep + 1)}`
}

/** Monaco language id from a file path. */
export function languageFromPath(path: string): string {
  const ext = path.slice(path.lastIndexOf('.') + 1).toLowerCase()
  const map: Record<string, string> = {
    ts: 'typescript',
    tsx: 'typescript',
    js: 'javascript',
    jsx: 'javascript',
    mjs: 'javascript',
    cjs: 'javascript',
    json: 'json',
    py: 'python',
    md: 'markdown',
    markdown: 'markdown',
    css: 'css',
    scss: 'scss',
    less: 'less',
    html: 'html',
    htm: 'html',
    xml: 'xml',
    yml: 'yaml',
    yaml: 'yaml',
    toml: 'ini',
    ini: 'ini',
    sh: 'shell',
    bash: 'shell',
    ps1: 'powershell',
    sql: 'sql',
    go: 'go',
    rs: 'rust',
    java: 'java',
    kt: 'kotlin',
    c: 'c',
    h: 'c',
    cpp: 'cpp',
    cc: 'cpp',
    hpp: 'cpp',
    cs: 'csharp',
    rb: 'ruby',
    php: 'php',
    vue: 'html',
    svelte: 'html',
    dockerfile: 'dockerfile',
  }
  const base = path.split(/[\\/]/).pop() ?? ''
  if (base.toLowerCase() === 'dockerfile') return 'dockerfile'
  return map[ext] ?? 'plaintext'
}
