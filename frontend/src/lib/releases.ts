import { getHealth } from '../api'

export const REPOSITORY_URL = 'https://github.com/ctianye/CodeSlice'
const RELEASE_API = 'https://api.github.com/repos/ctianye/CodeSlice/releases/latest'

export interface AppRelease {
  version: string
  url: string
  downloadUrl: string | null
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

export async function checkForRelease(signal?: AbortSignal): Promise<{ currentVersion: string; release: AppRelease | null }> {
  const controller = new AbortController()
  const cancel = () => controller.abort()
  signal?.addEventListener('abort', cancel, { once: true })
  if (signal?.aborted) controller.abort()
  const timeout = window.setTimeout(cancel, 12000)
  try {
    // Never substitute a fake version while the local service is still loading.
    const health = await getHealth(controller.signal)
    const response = await fetch(RELEASE_API, {
      headers: { Accept: 'application/vnd.github+json' }, signal: controller.signal,
    })
    if (!response.ok) throw new Error(response.status === 404 ? '仓库尚未发布正式版本。' : `GitHub 检查失败（HTTP ${response.status}）`)
    const latest = await response.json() as { tag_name?: string; html_url?: string; body?: string; prerelease?: boolean; draft?: boolean; assets?: Array<{ name: string; browser_download_url: string }> }
    const version = latest.tag_name ?? ''
    const newer = compareVersions(version, health.version) > 0
    const url = githubUrl(latest.html_url, '/ctianye/CodeSlice/releases/tag/')
    if (!url) throw new Error('GitHub 返回的 Release 地址无效。')
    const installer = latest.assets?.find((asset) => /^CodeSlice-.*-Setup\.exe$/i.test(asset.name))
    const downloadUrl = githubUrl(installer?.browser_download_url, '/ctianye/CodeSlice/releases/download/')
    return {
      currentVersion: health.version,
      release: newer && !latest.prerelease && !latest.draft ? {
        version: version.replace(/^v/, ''), url, downloadUrl, notes: typeof latest.body === 'string' ? latest.body : '',
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
