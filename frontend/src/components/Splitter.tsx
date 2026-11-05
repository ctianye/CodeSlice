import type { MouseEvent as ReactMouseEvent, ReactNode } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

interface SplitterProps {
  /** Current size of the pane before the splitter, in pixels. */
  value: number
  onChange: (next: number) => void
  min?: number
  max?: number
  /** When true the pane before the splitter grows as the pointer moves right. */
  invert?: boolean
  label: string
}

/**
 * Hand-written draggable divider — avoids pulling in a layout library for what
 * is a 40-line behaviour. Keyboard accessible via arrow keys.
 */
export function Splitter({ value, onChange, min = 160, max = 900, invert = false, label }: SplitterProps): ReactNode {
  const [dragging, setDragging] = useState(false)
  const start = useRef({ x: 0, value })

  const clamp = useCallback((next: number) => Math.round(Math.min(max, Math.max(min, next))), [min, max])

  useEffect(() => {
    if (!dragging) return
    const onMove = (event: MouseEvent) => {
      const delta = (event.clientX - start.current.x) * (invert ? -1 : 1)
      onChange(clamp(start.current.value + delta))
    }
    const onUp = () => setDragging(false)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [dragging, invert, onChange, clamp])

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 40 : 10
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      onChange(clamp(value + (invert ? step : -step)))
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      onChange(clamp(value + (invert ? -step : step)))
    }
  }

  return (
    <div
      className={`splitter${dragging ? ' dragging' : ''}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={value}
      tabIndex={0}
      onMouseDown={(event: ReactMouseEvent<HTMLDivElement>) => {
        event.preventDefault()
        start.current = { x: event.clientX, value }
        setDragging(true)
      }}
      onKeyDown={onKeyDown}
    />
  )
}
