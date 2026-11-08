import type { CSSProperties, ReactNode } from 'react'
import { createContext, Fragment, memo, useContext, useMemo, useState } from 'react'
import { useApp } from '../state/AppState'
import { Icon } from './Icon'

/**
 * Small, dependency-free Markdown renderer for assistant answers.
 *
 * The model writes Markdown (headings, lists, fenced code, inline code, bold,
 * links) and the previous version dumped it into a `white-space: pre-wrap`
 * div, so every answer looked like raw source. This renders the subset that
 * actually shows up in a code-reading conversation:
 *
 *   headings, paragraphs, bullet/ordered lists, blockquotes, rules,
 *   fenced code blocks (with language label + copy), pipe tables, inline code,
 *   bold, italic, links and bare `path/to/file.py:12-30` citation references.
 *
 * It deliberately does NOT support raw HTML (nothing is dangerouslySetInnerHTML)
 * or nested block structures — a chat bubble does not need them and every added
 * rule is a way to mangle a diff pasted by the model.
 *
 * Images are supported behind an optional `resolveImage` hook. A chat answer has
 * no file to resolve `![](docs/x.png)` against, so without the hook the syntax
 * degrades to its alt text; the document reader passes a resolver that turns a
 * repo-relative path into an `/asset` URL.
 */
export interface MarkdownProps {
  text: string
  /** Maps an image `src` from the source to a loadable URL, or null to skip it. */
  resolveImage?: (src: string) => string | null
  /** Text shown under an image that could not be resolved. */
  imageFallback?: (src: string) => string
}

export const Markdown = memo(function Markdown({
  text,
  resolveImage,
  imageFallback,
}: MarkdownProps): ReactNode {
  const blocks = useMemo(() => parseBlocks(text), [text])
  return (
    <ImageResolver.Provider value={resolveImage ?? null}>
      <div className="md">
        {blocks.map((block, index) => (
          <Block
            key={index}
            block={block}
            resolveImage={resolveImage}
            imageFallback={imageFallback}
          />
        ))}
      </div>
    </ImageResolver.Provider>
  )
})

/**
 * Carries the image resolver down to inline tokens.
 *
 * Block-level images get it as a prop, but an image inside a list item or a
 * paragraph goes through `Inline` → `TokenNode`, two levels that would otherwise
 * need the prop threaded through every call site.
 */
const ImageResolver = createContext<((src: string) => string | null) | null>(null)

/* --------------------------------- parsing -------------------------------- */

type Block =
  | { kind: 'p'; text: string }
  | { kind: 'h'; level: number; text: string }
  | { kind: 'ul'; items: string[] }
  | { kind: 'ol'; items: string[] }
  | { kind: 'quote'; text: string }
  | { kind: 'hr' }
  | { kind: 'code'; lang: string; code: string }
  | { kind: 'table'; head: string[]; align: Align[]; rows: string[][] }
  | { kind: 'img'; alt: string; src: string }

type Align = 'left' | 'center' | 'right' | null

/** Opening fence: three or more backticks/tildes, optionally followed by a language. */
const FENCE = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/
/** A closing fence is a bare run of the same character, at least as long as the opener. */
const CLOSING_FENCE = /^\s*(`{3,}|~{3,})\s*$/
const HEADING = /^(#{1,6})\s+(.*)$/
const UL = /^\s*[-*+]\s+(.*)$/
const OL = /^\s*(\d+)[.)]\s+(.*)$/
const QUOTE = /^\s*>\s?(.*)$/
const HR = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/
/** A table delimiter row: |---|:---:|---:| */
const TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/
/** A standalone image on its own line: `![alt](src)` or `![alt](src "title")`. */
const IMAGE = /^\s*!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)\s*$/

function splitRow(line: string): string[] {
  let trimmed = line.trim()
  if (trimmed.startsWith('|')) trimmed = trimmed.slice(1)
  if (trimmed.endsWith('|')) trimmed = trimmed.slice(0, -1)
  const cells: string[] = []
  let current = ''
  let inCode = false
  for (let i = 0; i < trimmed.length; i += 1) {
    const char = trimmed[i]
    if (char === '\\' && trimmed[i + 1] === '|') {
      current += '|'
      i += 1
      continue
    }
    if (char === '`') inCode = !inCode
    if (char === '|' && !inCode) {
      cells.push(current.trim())
      current = ''
      continue
    }
    current += char
  }
  cells.push(current.trim())
  return cells
}

function isTableStart(line: string, next: string | undefined): boolean {
  if (next === undefined) return false
  if (!line.includes('|')) return false
  return TABLE_SEP.test(next) && next.includes('-')
}

function parseAlign(sep: string): Align[] {
  return splitRow(sep).map((cell) => {
    const left = cell.startsWith(':')
    const right = cell.endsWith(':')
    if (left && right) return 'center'
    if (right) return 'right'
    if (left) return 'left'
    return null
  })
}

function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  const blocks: Block[] = []
  let i = 0

  const flushParagraph = (buffer: string[]) => {
    if (buffer.length === 0) return
    blocks.push({ kind: 'p', text: buffer.join('\n') })
    buffer.length = 0
  }

  const paragraph: string[] = []

  while (i < lines.length) {
    const line = lines[i] ?? ''

    // fenced code
    const fence = FENCE.exec(line)
    if (fence) {
      flushParagraph(paragraph)
      const marker = fence[1] ?? '```'
      const lang = (fence[2] ?? '').trim()
      const body: string[] = []
      i += 1
      // A note can quote a file that itself contains fences (a Markdown snippet,
      // for instance). Only a bare run at least as long as the opener closes the
      // block, so an inner ``` does not end the outer one early.
      while (i < lines.length) {
        const closing = CLOSING_FENCE.exec(lines[i] ?? '')
        if (closing && closing[1] !== undefined) {
          const run = closing[1]
          if (run[0] === marker[0] && run.length >= marker.length) break
        }
        body.push(lines[i] ?? '')
        i += 1
      }
      i += 1 // consume closing fence (or run off the end while streaming)
      blocks.push({ kind: 'code', lang, code: body.join('\n') })
      continue
    }

    if (line.trim() === '') {
      flushParagraph(paragraph)
      i += 1
      continue
    }

    const heading = HEADING.exec(line)
    if (heading) {
      flushParagraph(paragraph)
      blocks.push({ kind: 'h', level: Math.min(6, (heading[1] ?? '#').length), text: heading[2] ?? '' })
      i += 1
      continue
    }

    if (HR.test(line)) {
      flushParagraph(paragraph)
      blocks.push({ kind: 'hr' })
      i += 1
      continue
    }

    // A screenshot line in a README. Only whole-line images become blocks; an
    // image mixed into prose (a badge row, usually) is handled inline below.
    const image = IMAGE.exec(line)
    if (image) {
      flushParagraph(paragraph)
      blocks.push({ kind: 'img', alt: image[1] ?? '', src: image[2] ?? '' })
      i += 1
      continue
    }

    if (isTableStart(line, lines[i + 1])) {
      flushParagraph(paragraph)
      const head = splitRow(line)
      const align = parseAlign(lines[i + 1] ?? '')
      const rows: string[][] = []
      i += 2
      while (i < lines.length) {
        const row = lines[i] ?? ''
        if (row.trim() === '' || !row.includes('|')) break
        rows.push(splitRow(row))
        i += 1
      }
      blocks.push({ kind: 'table', head, align, rows })
      continue
    }

    if (UL.test(line)) {
      flushParagraph(paragraph)
      const items: string[] = []
      while (i < lines.length) {
        const match = UL.exec(lines[i] ?? '')
        if (!match) break
        items.push(match[1] ?? '')
        i += 1
      }
      blocks.push({ kind: 'ul', items })
      continue
    }

    if (OL.test(line)) {
      flushParagraph(paragraph)
      const items: string[] = []
      while (i < lines.length) {
        const match = OL.exec(lines[i] ?? '')
        if (!match) break
        items.push(match[2] ?? '')
        i += 1
      }
      blocks.push({ kind: 'ol', items })
      continue
    }

    if (QUOTE.test(line)) {
      flushParagraph(paragraph)
      const quoted: string[] = []
      while (i < lines.length) {
        const match = QUOTE.exec(lines[i] ?? '')
        if (!match) break
        quoted.push(match[1] ?? '')
        i += 1
      }
      blocks.push({ kind: 'quote', text: quoted.join('\n') })
      continue
    }

    paragraph.push(line)
    i += 1
  }

  flushParagraph(paragraph)
  return blocks
}

/* -------------------------------- rendering ------------------------------- */

function Block({
  block,
  resolveImage,
  imageFallback,
}: {
  block: Block
  resolveImage?: (src: string) => string | null
  imageFallback?: (src: string) => string
}): ReactNode {
  switch (block.kind) {
    case 'h': {
      const content = <Inline text={block.text} />
      if (block.level <= 2) return <h3 className="md-h1">{content}</h3>
      if (block.level === 3) return <h4 className="md-h2">{content}</h4>
      return <h5 className="md-h3">{content}</h5>
    }
    case 'ul':
      return (
        <ul className="md-ul">
          {block.items.map((item, index) => (
            <li key={index}>
              <Inline text={item} />
            </li>
          ))}
        </ul>
      )
    case 'ol':
      return (
        <ol className="md-ol">
          {block.items.map((item, index) => (
            <li key={index}>
              <Inline text={item} />
            </li>
          ))}
        </ol>
      )
    case 'quote':
      return (
        <blockquote className="md-quote">
          <Inline text={block.text} />
        </blockquote>
      )
    case 'hr':
      return <hr className="md-hr" />
    case 'table':
      return (
        <div className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>
                {block.head.map((cell, index) => (
                  <th key={index} style={alignStyle(block.align[index])}>
                    <Inline text={cell} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {block.head.map((_, cellIndex) => (
                    <td key={cellIndex} style={alignStyle(block.align[cellIndex])}>
                      <Inline text={row[cellIndex] ?? ''} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    case 'code':
      return <CodeBlock lang={block.lang} code={block.code} />
    case 'img':
      return <MarkdownImage alt={block.alt} src={block.src} resolve={resolveImage} fallback={imageFallback} />
    default:
      return (
        <p className="md-p">
          <Inline text={block.text} />
        </p>
      )
  }
}

function alignStyle(align: Align | undefined): CSSProperties | undefined {
  return align ? { textAlign: align } : undefined
}

/**
 * One image block.
 *
 * Document previews resolve the source through `resolveImage`; a chat answer
 * passes nothing, so the alt text is shown instead of a broken-image icon.
 * A resolver that returns `null` (unknown scheme, escaping path, missing file)
 * falls back to the same alt text — silently dropping an image would hide a
 * missing screenshot.
 */
function MarkdownImage({
  alt,
  src,
  resolve,
  fallback,
}: {
  alt: string
  src: string
  resolve?: (src: string) => string | null
  fallback?: (src: string) => string
}): ReactNode {
  const url = resolve ? resolve(src) : null
  if (!url) {
    return <span className="md-img-missing">{fallback ? fallback(src) : alt || src}</span>
  }
  return (
    <span className="md-img">
      <img src={url} alt={alt} loading="lazy" />
      {alt ? <span className="md-img-alt">{alt}</span> : null}
    </span>
  )
}

function CodeBlock({ lang, code }: { lang: string; code: string }): ReactNode {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    void navigator.clipboard
      ?.writeText(code)
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1400)
      })
      .catch(() => undefined)
  }

  return (
    <div className="md-code">
      <div className="md-code-head">
        <span className="md-code-lang">{lang || 'text'}</span>
        <span className="spacer" />
        <button className="md-copy" onClick={copy} title="复制代码" type="button">
          <Icon name={copied ? 'check' : 'copy'} size={12} />
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  )
}

/* --------------------------------- inlines -------------------------------- */

/** `path/file.py:12-30` — a citation the model wrote inline. */
const CITE = /(^|[\s(（[`"'])([\w@./\\-]+\.[A-Za-z0-9]{1,8}):L?(\d+)(?:[-–](\d+))?(?=$|[\s),.;:，。；）\]`"'])/g

interface Token {
  type: 'text' | 'code' | 'bold' | 'italic' | 'link' | 'cite' | 'img'
  text: string
  href?: string
  path?: string
  start?: number
  end?: number
}

function tokenize(text: string): Token[] {
  const tokens: Token[] = []
  // order matters: code first (its contents are literal), then images (before
  // links, because `![a](b)` also matches the link pattern from `[`), bold, italic
  const pattern =
    /(`[^`\n]+`)|(!\[[^\]\n]*\]\([^)\s]+\))|(\[[^\]\n]+\]\([^)\s]+\))|(\*\*[^*\n]+\*\*)|(__[^_\n]+__)|(\*[^*\n]+\*)|(_[^_\n]+_)/g
  let last = 0
  let match: RegExpExecArray | null

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) pushPlain(tokens, text.slice(last, match.index))
    const raw = match[0]
    if (raw.startsWith('`')) {
      tokens.push({ type: 'code', text: raw.slice(1, -1) })
    } else if (raw.startsWith('![')) {
      const cut = raw.indexOf('](')
      tokens.push({ type: 'img', text: raw.slice(2, cut), href: raw.slice(cut + 2, -1) })
    } else if (raw.startsWith('[')) {
      const cut = raw.indexOf('](')
      tokens.push({ type: 'link', text: raw.slice(1, cut), href: raw.slice(cut + 2, -1) })
    } else if (raw.startsWith('**') || raw.startsWith('__')) {
      tokens.push({ type: 'bold', text: raw.slice(2, -2) })
    } else {
      tokens.push({ type: 'italic', text: raw.slice(1, -1) })
    }
    last = match.index + raw.length
  }
  if (last < text.length) pushPlain(tokens, text.slice(last))
  return tokens
}

function pushPlain(tokens: Token[], chunk: string): void {
  let last = 0
  let match: RegExpExecArray | null
  CITE.lastIndex = 0
  while ((match = CITE.exec(chunk)) !== null) {
    const prefix = match[1] ?? ''
    const path = match[2] ?? ''
    const start = Number(match[3])
    const end = match[4] ? Number(match[4]) : start
    const begin = match.index + prefix.length
    if (begin > last) tokens.push({ type: 'text', text: chunk.slice(last, begin) })
    tokens.push({ type: 'cite', text: `${path}:${start}${match[4] ? `-${end}` : ''}`, path, start, end })
    last = match.index + match[0].length
  }
  if (last < chunk.length) tokens.push({ type: 'text', text: chunk.slice(last) })
}

function Inline({ text }: { text: string }): ReactNode {
  const tokens = useMemo(() => tokenize(text), [text])
  return (
    <>
      {tokens.map((token, index) => (
        <Fragment key={index}>
          <TokenNode token={token} />
        </Fragment>
      ))}
    </>
  )
}

function TokenNode({ token }: { token: Token }): ReactNode {
  const { reveal } = useApp()
  const resolveImage = useContext(ImageResolver)

  switch (token.type) {
    case 'code':
      return <code className="md-inline-code">{token.text}</code>
    case 'img': {
      // A badge row (`![](shield.svg)` repeated inline) must not break the
      // paragraph, so an unresolvable inline image collapses to its alt text.
      const url = resolveImage ? resolveImage(token.href ?? '') : null
      if (!url) return <>{token.text}</>
      return <img className="md-inline-img" src={url} alt={token.text} loading="lazy" />
    }
    case 'bold':
      return <strong>{token.text}</strong>
    case 'italic':
      return <em>{token.text}</em>
    case 'link':
      return (
        <a href={token.href} target="_blank" rel="noreferrer noopener">
          {token.text}
        </a>
      )
    case 'cite':
      return (
        <button
          type="button"
          className="md-cite"
          title={`跳转到 ${token.path} 第 ${token.start}-${token.end} 行`}
          onClick={() => reveal(token.path ?? '', token.start, token.end)}
        >
          {token.text}
        </button>
      )
    default:
      return <>{token.text}</>
  }
}
