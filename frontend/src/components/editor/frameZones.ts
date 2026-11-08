import type { editor as MonacoEditor } from 'monaco-editor'

export interface FrameZoneItem {
  key: string
  /** First line of the framed range. */
  start: number
  /** The conversation's note title when it has one, otherwise its question. */
  label: string
  active: boolean
  onClick: () => void
  annotation?: string
  expanded?: boolean
  onToggle?: () => void
  onDelete?: () => void
}

interface Zone {
  id: string
  node: HTMLElement
  label: HTMLElement
  body?: HTMLElement
  height: number
}

/**
 * A title strip reserved directly above each framed range.
 *
 * An absolutely positioned overlay would cover a line of code, so the strip is
 * a Monaco view zone instead: it is part of the normal layout, scrolls with the
 * text and hides nothing. Zones are created once per range and only their
 * contents change afterwards, so the view does not jump while reading.
 */
export function createFrameZones(editor: MonacoEditor.IStandaloneCodeEditor): {
  update: (items: FrameZoneItem[]) => void
  dispose: () => void
} {
  const zones = new Map<string, Zone>()
  // Latest items, so a zone's click handler never goes stale.
  let current = new Map<string, FrameZoneItem>()

  const build = (key: string, item: FrameZoneItem): Omit<Zone, 'id'> => {
    const node = document.createElement('div')
    node.className = 'range-zone'
    if (item.annotation) node.classList.add('annotation-zone')
    const label = document.createElement('button')
    label.type = 'button'
    label.className = 'range-flag'
    label.textContent = item.label
    label.title = item.label
    label.setAttribute('aria-label', item.annotation ? `展开或收起批注：${item.annotation}` : `跳转到对话：${item.label}`)
    if (item.annotation) label.setAttribute('aria-expanded', String(Boolean(item.expanded)))
    label.addEventListener('mousedown', (event) => event.stopPropagation())
    label.addEventListener('click', (event) => {
      event.stopPropagation()
      const latest = current.get(key)
      if (latest?.onToggle) latest.onToggle()
      else latest?.onClick()
    })
    label.title = item.annotation ? '展开或收起批注' : item.label
    node.append(label)
    if (item.onDelete) {
      const remove = document.createElement('button')
      remove.type = 'button'
      remove.className = 'annotation-delete'
      remove.textContent = '删除'
      remove.setAttribute('aria-label', '删除批注')
      remove.addEventListener('click', (event) => { event.stopPropagation(); current.get(key)?.onDelete?.() })
      node.append(remove)
    }
    let body: HTMLElement | undefined
    if (item.annotation && item.expanded) {
      body = document.createElement('div')
      body.className = 'annotation-zone-body'
      body.textContent = item.annotation
      node.append(body)
    }
    return { node, label, body, height: item.annotation && item.expanded ? 150 : ZONE_HEIGHT }
  }

  const update = (items: FrameZoneItem[]) => {
    current = new Map(items.map((item) => [item.key, item]))
    const seen = new Set<string>()

    for (const item of items) {
      seen.add(item.key)
      let existing = zones.get(item.key)
      if (existing) {
        const nextHeight = item.annotation && item.expanded ? 150 : ZONE_HEIGHT
        if (existing.height !== nextHeight) {
          editor.changeViewZones((accessor) => accessor.removeZone(existing!.id))
          zones.delete(item.key)
          existing = undefined
        }
      }
      if (existing) {
        if (existing.label.textContent !== item.label) existing.label.textContent = item.label
        if (existing.label.title !== item.label) existing.label.title = item.label
        existing.label.classList.toggle('active', item.active)
        continue
      }
      const { node, label } = build(item.key, item)
      label.classList.toggle('active', item.active)
      // `changeViewZones` returns nothing, so the zone id is captured in a
      // closure rather than as its return value.
      let id = ''
      editor.changeViewZones((accessor) => {
        id = accessor.addZone({
          // `0` hangs the strip above the first line for ranges that start there.
          afterLineNumber: Math.max(0, item.start - 1),
          heightInPx: item.annotation && item.expanded ? 150 : ZONE_HEIGHT,
          domNode: node,
          suppressMouseDown: true,
        })
      })
      zones.set(item.key, { id, node, label, height: item.annotation && item.expanded ? 150 : ZONE_HEIGHT })
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

/** Must match the `height` of `.range-zone` in `styles/editor.css`. */
const ZONE_HEIGHT = 19
