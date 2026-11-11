import type { Monaco } from '@monaco-editor/react'
import type { HoverResult } from '../../types'
import { getHover } from '../../api'

/**
 * Monaco stays a dumb view: F12 / Ctrl+Click are handled by CodePane so the
 * app's tab model (not Monaco's model swap) decides which file is open.
 *
 * The only global registration here is hover. It has no navigation side effect,
 * and a module-level context avoids re-registering the provider (and duplicating
 * tooltips) every time the editor mounts.
 */
interface NavigationContext {
  projectId: number | null
  path: string | null
}

let context: NavigationContext = { projectId: null, path: null }
let registered = false

const hoverCache = new Map<string, HoverResult | null>()
const HOVER_CACHE_LIMIT = 300

export function setNavigationContext(next: NavigationContext | null): void {
  // Hover answers are position-based; a different file invalidates all of them.
  if ((next?.path ?? null) !== context.path) hoverCache.clear()
  context = next ?? { projectId: null, path: null }
}

/** Drop cached hover cards after a save or index refresh. */
export function clearNavigationHoverCache(): void {
  hoverCache.clear()
}

function rememberHover(key: string, value: HoverResult | null): void {
  if (hoverCache.size >= HOVER_CACHE_LIMIT) {
    const oldest = hoverCache.keys().next().value
    if (oldest !== undefined) hoverCache.delete(oldest)
  }
  hoverCache.set(key, value)
}

const NAVIGATION_LANGUAGES = [
  'python',
  'typescript',
  'javascript',
  'go',
  'rust',
  'java',
  'c',
  'cpp',
  'shell',
]

// VS Code's textual occurrence provider (`TextualDocumentHighlightProvider`)
// uses the editor's word separators; Monaco standalone does not register that
// provider, so we supply the same whole-word search ourselves.
const WORD_SEPARATORS = '~!@#$%^&*()-=+[{]}\\|;:\'",.<>/?'

export function registerCodeNavigation(monaco: Monaco): void {
  if (registered) return
  registered = true

  monaco.languages.registerDocumentHighlightProvider(NAVIGATION_LANGUAGES, {
    provideDocumentHighlights: (model, position) => {
      const word = model.getWordAtPosition(position)
      if (!word?.word) return []
      // Whole-word, case-sensitive, capped: a common name like `run` should not
      // turn a large file into thousands of decorations.
      return model
        .findMatches(word.word, false, false, true, WORD_SEPARATORS, false, 500)
        .map((match) => ({
          range: match.range,
          kind: monaco.languages.DocumentHighlightKind.Text,
        }))
    },
  })

  monaco.languages.registerHoverProvider(NAVIGATION_LANGUAGES, {
    provideHover: async (model, position) => {
      const { projectId, path } = context
      if (projectId === null || !path) return null
      // The editor only ever shows the active tab, but a stale request can
      // outlive a tab switch; never answer for a different file. `uri.path` is
      // percent-encoded, while project paths are raw.
      const rawPath = model.uri.path.replace(/^\/+/, '')
      let modelPath = rawPath
      try {
        modelPath = decodeURIComponent(rawPath)
      } catch {
        // A malformed escape is not worth failing a hover over.
      }
      if (modelPath && modelPath !== path && !modelPath.endsWith(`/${path}`)) return null

      const key = `${projectId}:${path}:${position.lineNumber}:${position.column}`
      let result = hoverCache.get(key)
      if (result === undefined) {
        try {
          result = await getHover(projectId, path, position.lineNumber, position.column)
        } catch {
          result = null
        }
        rememberHover(key, result)
      }
      if (!result?.contents) return null
      if (context.projectId !== projectId || context.path !== path) return null
      // Monaco applies its `hoverHighlight` decoration to the returned range.
      // That must be the *word under the cursor* (VS Code behaviour), not the
      // definition range from the backend — the definition usually lives in a
      // different file.
      const word = model.getWordAtPosition(position)
      const range = word
        ? new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn)
        : undefined
      return {
        contents: [
          {
            value: `${result.contents}\n\n---\n*F12 / Ctrl+Click 转到定义 · Shift+F12 查找引用*`,
          },
        ],
        range,
      }
    },
  })
}
