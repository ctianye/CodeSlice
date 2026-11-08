import type { ReactNode } from 'react'
import { useMemo } from 'react'
import { assetUrl } from '../api'
import { Markdown } from './Markdown'

export interface MarkdownDocProps {
  /** Project-relative path of the document being rendered (used to resolve images and links). */
  path: string
  projectId: number
  text: string
}

/**
 * Read a Markdown document the way it is meant to be read.
 *
 * The editor pane used to show `.md` files as source, so a README opened in
 * CodeSlice looked like a wall of `#` and backticks. This renders the same
 * subset the chat bubbles use, at document scale, and resolves the thing a
 * repository document needs that a chat answer does not: images.
 *
 * Image sources are resolved relative to the document's own directory, which is
 * how a README referring to `docs/screenshots/welcome.png` is written. They are
 * served by the project asset endpoint rather than read from disk, because the
 * renderer has no filesystem access.
 */
export function MarkdownDoc({ path, projectId, text }: MarkdownDocProps): ReactNode {
  const resolveImage = useMemo(() => {
    const base = directoryOf(path)
    return (src: string): string | null => {
      const rel = resolveRelative(base, src)
      // Remote images are left alone: the CSP only allows same-origin and
      // http://127.0.0.1, so a CDN URL would fail anyway and the alt text reads
      // better than a broken frame.
      if (!rel) return null
      return assetUrl(projectId, rel)
    }
  }, [path, projectId])

  const imageFallback = useMemo(
    () => (src: string): string => `图片未找到：${src}`,
    [],
  )

  return (
    <div className="doc-scroll">
      <article className="doc-doc">
        <Markdown text={text} resolveImage={resolveImage} imageFallback={imageFallback} />
      </article>
    </div>
  )
}

/** Directory part of a project-relative path, with `''` for the repository root. */
function directoryOf(path: string): string {
  const cut = path.lastIndexOf('/')
  return cut === -1 ? '' : path.slice(0, cut)
}

/**
 * Join a Markdown `src` onto the directory of the document.
 *
 * Returns null for anything that is not a repository-relative path: absolute
 * URLs, protocol-relative `//host`, data URIs and paths that climb above the
 * repository root with `..`. Those either cannot be served by the asset endpoint
 * or would escape the project.
 */
export function resolveRelative(base: string, src: string): string | null {
  const raw = src.trim()
  if (!raw) return null
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw)) return null // scheme: http:, data:, file:
  if (raw.startsWith('//')) return null
  // A repository document may use an absolute path (`/docs/x.png`) to mean "from
  // the repository root", which is how GitHub renders it.
  const start = raw.startsWith('/') ? '' : base
  const segments = (start ? `${start}/${raw.replace(/^\//, '')}` : raw.replace(/^\//, '')).split('/')
  const out: string[] = []
  for (const segment of segments) {
    if (segment === '' || segment === '.') continue
    if (segment === '..') {
      if (out.length === 0) return null
      out.pop()
      continue
    }
    out.push(segment)
  }
  return out.length > 0 ? out.join('/') : null
}
