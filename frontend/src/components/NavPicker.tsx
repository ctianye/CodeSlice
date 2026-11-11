import type { ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'

/** One row in the definition/reference picker. */
export interface NavChoice {
  id: string
  label: string
  hint: string
  meta?: string
  badge?: string
  path: string
  line: number
  end_line: number
  /** Definition name, when the row is a definition rather than a reference. */
  symbolName?: string
}

interface NavPickerProps {
  title: string
  subtitle?: string
  items: NavChoice[]
  onPick: (item: NavChoice) => void
  onClose: () => void
}

/**
 * A light keyboard-first picker for the two cases that must not be guessed:
 * an ambiguous definition name, and "find references" returning many sites.
 * It stays inside the editor pane instead of opening a modal so the code behind
 * it remains readable.
 */
export function NavPicker({ title, subtitle, items, onPick, onClose }: NavPickerProps): ReactNode {
  const [index, setIndex] = useState(0)
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setIndex((current) => (current + 1) % Math.max(1, items.length))
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setIndex((current) => (current - 1 + items.length) % Math.max(1, items.length))
        return
      }
      if (event.key === 'Enter') {
        event.preventDefault()
        const item = items[index]
        if (item) onPick(item)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [index, items, onClose, onPick])

  useEffect(() => {
    const active = itemRefs.current[index]
    active?.focus()
    active?.scrollIntoView({ block: 'nearest' })
  }, [index])

  if (items.length === 0) return null

  return (
    <div className="nav-picker-backdrop" onMouseDown={onClose}>
      <div
        className="nav-picker"
        role="dialog"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="nav-picker-head">
          <div className="nav-picker-titles">
            <strong>{title}</strong>
            {subtitle ? <span>{subtitle}</span> : null}
          </div>
          <span className="nav-picker-count">{items.length}</span>
          <button className="ghost icon" onClick={onClose} title="关闭 (Esc)" aria-label="关闭" type="button">
            <Icon name="x" size={12} />
          </button>
        </div>
        <div className="nav-picker-list" role="listbox">
          {items.map((item, position) => (
            <button
              key={item.id}
              ref={(element) => { itemRefs.current[position] = element }}
              className={`nav-picker-item${position === index ? ' active' : ''}`}
              role="option"
              aria-selected={position === index}
              onMouseEnter={() => setIndex(position)}
              onClick={() => onPick(item)}
              type="button"
            >
              <span className="nav-picker-name">{item.label}</span>
              <span className="nav-picker-hint">{item.hint}</span>
              <span className="nav-picker-meta">
                {item.badge ? <em>{item.badge}</em> : null}
                {item.meta}
              </span>
            </button>
          ))}
        </div>
        <div className="nav-picker-foot">
          <kbd>↑</kbd><kbd>↓</kbd> 选择 · <kbd>Enter</kbd> 跳转 · <kbd>Esc</kbd> 关闭
        </div>
      </div>
    </div>
  )
}
