import type { editor as MonacoEditor } from 'monaco-editor'

export interface FrameAnnotation {
  title: string
  body: string
}

export interface FrameZoneItem {
  key: string
  /** First line of the framed range. */
  start: number
  /** The conversation's note title when it has one, otherwise its question. */
  label: string
  active: boolean
  onClick: () => void
  /** Present on annotation zones: a collapsed title plus the full body. */
  annotation?: FrameAnnotation
  expanded?: boolean
  onToggle?: () => void
  onDelete?: () => void
}

interface Zone {
  id: string
  signature: string
  height: number
}

/** Must match the height of `.range-zone` in `styles/editor.css`. */
const ZONE_HEIGHT = 19
/** Header row of an annotation card. */
const ANNOTATION_HEAD_HEIGHT = 24
/** Expanded card height: enough for several lines, then the body scrolls. */
const ANNOTATION_EXPANDED_HEIGHT = 104

/**
 * A title strip reserved directly above each framed range.
 *
 * An absolutely positioned overlay would cover a line of code, so the strip is
 * a Monaco view zone instead: it is part of the normal layout, scrolls with the
 * text and hides nothing. The DOM is rebuilt only when the item's visible text
 * or height changes, so expanding one annotation never disturbs the others.
 */
export function createFrameZones(editor: MonacoEditor.IStandaloneCodeEditor): {
  update: (items: FrameZoneItem[]) => void
  dispose: () => void
} {
  const zones = new Map<string, Zone>()
  // Latest items, so a zone's click handler never goes stale.
  let current = new Map<string, FrameZoneItem>()

  const zoneHeight = (item: FrameZoneItem): number => {
    if (!item.annotation) return ZONE_HEIGHT
    return item.expanded ? ANNOTATION_EXPANDED_HEIGHT : ANNOTATION_HEAD_HEIGHT
  }

  const signatureFor = (item: FrameZoneItem): string => JSON.stringify([
    item.label,
    item.active,
    item.annotation?.title ?? '',
    item.annotation?.body ?? '',
    Boolean(item.expanded),
    Boolean(item.onDelete),
    zoneHeight(item),
  ])

  const buildConversation = (item: FrameZoneItem): HTMLElement => {
    const node = document.createElement('div')
    node.className = 'range-zone'
    const label = document.createElement('button')
    label.type = 'button'
    label.className = 'range-flag'
    label.textContent = item.label
    label.title = item.label
    label.setAttribute('aria-label', `跳转到对话：${item.label}`)
    label.addEventListener('mousedown', (event) => event.stopPropagation())
    label.addEventListener('click', (event) => {
      event.stopPropagation()
      current.get(item.key)?.onClick()
    })
    node.append(label)
    return node
  }

  const buildAnnotation = (item: FrameZoneItem): HTMLElement => {
    const annotation = item.annotation!
    const node = document.createElement('div')
    node.className = 'range-zone annotation-zone'
    const head = document.createElement('div')
    head.className = 'annotation-head'

    const toggle = document.createElement('button')
    toggle.type = 'button'
    toggle.className = 'annotation-toggle'
    toggle.title = item.expanded ? '收起批注' : '展开批注'
    toggle.setAttribute('aria-expanded', String(Boolean(item.expanded)))
    toggle.setAttribute('aria-label', `${item.expanded ? '收起' : '展开'}批注：${annotation.title}`)

    const chevron = document.createElement('span')
    chevron.className = 'annotation-chevron'
    chevron.textContent = item.expanded ? '▾' : '▸'
    const kind = document.createElement('span')
    kind.className = 'annotation-kind'
    kind.textContent = '批注'
    const title = document.createElement('span')
    title.className = 'annotation-title'
    title.textContent = annotation.title
    title.title = annotation.title
    toggle.append(chevron, kind, title)
    toggle.addEventListener('mousedown', (event) => event.stopPropagation())
    toggle.addEventListener('click', (event) => {
      event.stopPropagation()
      current.get(item.key)?.onToggle?.()
    })
    head.append(toggle)

    if (item.onDelete) {
      const remove = document.createElement('button')
      remove.type = 'button'
      remove.className = 'annotation-delete'
      remove.textContent = '删除'
      remove.title = '删除批注'
      remove.setAttribute('aria-label', '删除批注')
      remove.addEventListener('mousedown', (event) => event.stopPropagation())
      remove.addEventListener('click', (event) => {
        event.stopPropagation()
        current.get(item.key)?.onDelete?.()
      })
      head.append(remove)
    }
    node.append(head)

    if (item.expanded) {
      const body = document.createElement('div')
      body.className = 'annotation-zone-body'
      body.textContent = annotation.body
      node.append(body)
    }
    return node
  }

  const update = (items: FrameZoneItem[]) => {
    current = new Map(items.map((item) => [item.key, item]))
    const seen = new Set<string>()

    for (const item of items) {
      seen.add(item.key)
      const height = zoneHeight(item)
      const signature = signatureFor(item)
      let existing = zones.get(item.key)
      if (existing && (existing.height !== height || existing.signature !== signature)) {
        editor.changeViewZones((accessor) => accessor.removeZone(existing!.id))
        zones.delete(item.key)
        existing = undefined
      }
      if (existing) continue

      const node = item.annotation ? buildAnnotation(item) : buildConversation(item)
      // `changeViewZones` returns nothing, so the zone id is captured in a
      // closure rather than as its return value.
      let id = ''
      editor.changeViewZones((accessor) => {
        id = accessor.addZone({
          // `0` hangs the strip above the first line for ranges that start there.
          afterLineNumber: Math.max(0, item.start - 1),
          heightInPx: height,
          domNode: node,
          suppressMouseDown: true,
        })
      })
      zones.set(item.key, { id, signature, height })
    }

    editor.changeViewZones((accessor) => {
      for (const [key, zone] of zones) {
        if (seen.has(key)) continue
        accessor.removeZone(zone.id)
        zones.delete(key)
      }
    })
  }

  const dispose = () => {
    editor.changeViewZones((accessor) => {
      for (const zone of zones.values()) accessor.removeZone(zone.id)
    })
    zones.clear()
    current = new Map()
  }

  return { update, dispose }
}
