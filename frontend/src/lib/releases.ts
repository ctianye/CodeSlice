import { getHealth, getLatestRelease } from '../api'
import { bridge } from '../bridge'

export const REPOSITORY_URL = 'https://github.com/ctianye/CodeSlice'

export interface AppRelease {
  version: string
  url: string
  downloadUrl: string | null
  downloadLabel: string
  notes: string
}

function parseVersion(value: string): { numbers: number[]; pre: string[] } {
  const match = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/.exec(value.trim())
  if (!match) throw new Error(`无法识别版本号：${value}`)
  return { numbers: match.slice(1, 4).map(Number), pre: match[4]?.split('.') ?? [] }
}

/** Compare SemVer, including prereleases; build metadata has no precedence. */
export function compareVersions(left: string, right: string): number {
  const a = parseVersion(left)
  const b = parseVersion(right)
  for (let i = 0; i < 3; i += 1) {
    if (a.numbers[i] !== b.numbers[i]) return a.numbers[i] > b.numbers[i] ? 1 : -1
  }
  if (!a.pre.length || !b.pre.length) return a.pre.length === b.pre.length ? 0 : a.pre.length ? -1 : 1
  for (let i = 0; i < Math.max(a.pre.length, b.pre.length); i += 1) {
    const x = a.pre[i]
    const y = b.pre[i]
    if (x === y) continue
    if (x === undefined || y === undefined) return x === undefined ? -1 : 1
    const xn = /^\d+$/.test(x)
    const yn = /^\d+$/.test(y)
    if (xn && yn) return Number(x) > Number(y) ? 1 : -1
    if (xn !== yn) return xn ? -1 : 1
    return x > y ? 1 : -1
  }
  return 0
}

function githubUrl(value: unknown, prefix: string): string | null {
  if (typeof value !== 'string') return null
  try {
    const url = new URL(value)
    return url.origin === 'https://github.com' && url.pathname.startsWith(prefix) ? url.href : null
  } catch {
    return null
  }
}


/** The renderer's platform, with a browser-dev fallback (no Electron bridge). */
function currentTarget(): { platform: string; arch: string } {
  if (bridge?.platform) return { platform: bridge.platform, arch: bridge.arch }
  const ua = navigator.userAgent
  const platform = ua.includes('Mac') ? 'darwin' : ua.includes('Linux') ? 'linux' : 'win32'
  return { platform, arch: /arm64|aarch64/i.test(ua) ? 'arm64' : 'x64' }
}

/** Pick the installer for this machine from the release asset list. */
function pickDownload(assets: Array<{ name: string; browser_download_url: string }>): { url: string | null; label: string } {
  const { platform, arch } = currentTarget()
  const find = (pattern: RegExp) => assets.find((asset) => pattern.test(asset.name))
  let asset: { name: string; browser_download_url: string } | undefined
  let label = '查看 Release 下载'
  if (platform === 'win32') {
    asset = find(/^CodeSlice-.*-Setup\.exe$/i)
    label = '下载 Windows 安装包'
  } else if (platform === 'darwin') {
    asset = arch === 'arm64' ? find(/-arm64\.dmg$/i) : find(/-x64\.dmg$/i)
    label = arch === 'arm64' ? '下载 macOS 安装包（Apple Silicon）' : '下载 macOS 安装包（Intel）'
  } else if (platform === 'linux') {
    asset = arch === 'arm64' ? find(/-arm64\.AppImage$/i) : find(/-x86_64\.AppImage$/i)
    label = '下载 Linux AppImage'
  }
  return { url: githubUrl(asset?.browser_download_url, '/ctianye/CodeSlice/releases/download/'), label }
}

export async function checkForRelease(signal?: AbortSignal): Promise<{ currentVersion: string; release: AppRelease | null }> {
  const controller = new AbortController()
  const cancel = () => controller.abort()
  signal?.addEventListener('abort', cancel, { once: true })
  if (signal?.aborted) controller.abort()
  const timeout = window.setTimeout(cancel, 20000)
  try {
    // Never substitute a fake version while the local service is still loading.
    const health = await getHealth(controller.signal)
    // The backend owns the GitHub request: it caches a successful response and
    // falls back to the HTML/Atom feed when the unauthenticated API hits its
    // 60-requests-per-hour limit. Calling api.github.com from the renderer was
    // what surfaced HTTP 403 to the update panel.
    const latest = await getLatestRelease(controller.signal)
    const version = latest.tag_name ?? ''
    const newer = compareVersions(version, health.version) > 0
    const url = githubUrl(latest.html_url, '/ctianye/CodeSlice/releases/tag/')
    if (!url) throw new Error('GitHub 返回的 Release 地址无效。')
    const download = pickDownload(latest.assets ?? [])
    return {
      currentVersion: health.version,
      release: newer && !latest.prerelease && !latest.draft ? {
        version: version.replace(/^v/, ''), url, downloadUrl: download.url, downloadLabel: download.label,
        notes: typeof latest.body === 'string' ? latest.body : '',
      } : null,
    }
  } catch (error) {
    if (controller.signal.aborted && !signal?.aborted) throw new Error('检查更新超时，请稍后重试。')
    throw error
  } finally {
    window.clearTimeout(timeout)
    signal?.removeEventListener('abort', cancel)
  }
}
