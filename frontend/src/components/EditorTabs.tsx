import { useCallback, useEffect, useRef, useState } from 'react'
import { useApp } from '../state/AppState'
import type { EditorTab } from '../state/AppState'
import { Icon } from './Icon'
import { baseName } from '../lib/citations'

interface EditorTabsProps {
  /** Toggle the terminal dock; the switch lives at the right end of the strip. */
  onToggleTerminal: () => void
  terminalOpen: boolean
}

/**
 * Tab strip above the main pane.
 *
 * Behaves like a desktop editor's strip: click to focus, middle-click or the ×
 * to close, drag to reorder, right-click for "close others". Tabs shrink to a
 * minimum width and the strip scrolls horizontally instead of wrapping, so the
 * editor never loses vertical space to a second row of tabs.
 */
export function EditorTabs({ onToggleTerminal, terminalOpen }: EditorTabsProps): React.ReactNode {
  const { tabs, activeTabIndex, activateTab, closeTab, closeOtherTabs, moveTab } = useApp()
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [menuIndex, setMenuIndex] = useState<number | null>(null)
  const stripRef = useRef<HTMLDivElement | null>(null)
  const activeRef = useRef<HTMLButtonElement | null>(null)

  // Keep the active tab reachable when the strip is wider than the pane.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [activeTabIndex, tabs.length])

  // A context menu should not survive a click elsewhere in the app.
  useEffect(() => {
    if (menuIndex === null) return
    const close = () => setMenuIndex(null)
    window.addEventListener('mousedown', close)
    window.addEventListener('blur', close)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('blur', close)
    }
  }, [menuIndex])

  const onWheel = useCallback((event: React.WheelEvent<HTMLDivElement>) => {
    const strip = stripRef.current
    // A plain vertical wheel over the strip scrolls it sideways; editors do this
    // because the strip has no vertical overflow of its own.
    if (!strip || event.deltaY === 0 || event.shiftKey) return
    strip.scrollLeft += event.deltaY
  }, [])

  return (
    <div className="editor-tabs" role="tablist" aria-label="打开的文件与笔记">
      <div className="editor-tabs-strip" ref={stripRef} onWheel={onWheel}>
        {tabs.length === 0 ? <span className="editor-tabs-hint">还没有打开的文件</span> : null}
        {tabs.map((tab, index) => {
          const active = index === activeTabIndex
          const title = titleOf(tab)
          return (
            <div
              key={keyOf(tab)}
              className={`editor-tab${active ? ' active' : ''}${dragIndex === index ? ' dragging' : ''}`}
              role="tab"
              aria-selected={active}
              title={hintOf(tab)}
              draggable
              onDragStart={(event) => {
                setDragIndex(index)
                event.dataTransfer.effectAllowed = 'move'
                // Firefox refuses to start a drag without payload data.
                event.dataTransfer.setData('text/plain', String(index))
              }}
              onDragOver={(event) => {
                if (dragIndex === null || dragIndex === index) return
                event.preventDefault()
                event.dataTransfer.dropEffect = 'move'
              }}
              onDrop={(event) => {
                event.preventDefault()
                if (dragIndex !== null && dragIndex !== index) moveTab(dragIndex, index)
                setDragIndex(null)
              }}
              onDragEnd={() => setDragIndex(null)}
              onMouseDown={(event) => {
                // Middle click closes, like every editor and browser tab.
                if (event.button === 1) {
                  event.preventDefault()
                  closeTab(index)
                }
              }}
              onContextMenu={(event) => {
                event.preventDefault()
                setMenuIndex(index)
              }}
            >
              <button
                className="editor-tab-label"
                role="tab"
                ref={active ? activeRef : undefined}
                onClick={() => activateTab(index)}
                type="button"
              >
                <Icon name={tab.kind === 'note' ? 'notes' : 'file-code'} size={11} />
                <span className="editor-tab-title">{title}</span>
              </button>
              <button
                className="editor-tab-close"
                title="关闭"
                aria-label={`关闭 ${title}`}
                onClick={(event) => {
                  event.stopPropagation()
                  closeTab(index)
                }}
                type="button"
              >
                <Icon name="x" size={10} />
              </button>
              {menuIndex === index ? (
                <div className="editor-tab-menu" onMouseDown={(event) => event.stopPropagation()}>
                  <button
                    onClick={() => {
                      closeTab(index)
                      setMenuIndex(null)
                    }}
                    type="button"
                  >
                    关闭
                  </button>
                  <button
                    disabled={tabs.length < 2}
                    onClick={() => {
                      closeOtherTabs(index)
                      setMenuIndex(null)
                    }}
                    type="button"
                  >
                    关闭其他
                  </button>
                  <button
                    onClick={() => {
                      void navigator.clipboard?.writeText(tab.kind === 'file' ? tab.path : tab.note.title)
                      setMenuIndex(null)
                    }}
                    type="button"
                  >
                    {tab.kind === 'file' ? '复制路径' : '复制标题'}
                  </button>
                </div>
              ) : null}
            </div>
          )
        })}
      </div>
      <span className="editor-tabs-spacer" />
      <button
        className={`editor-tabs-action${terminalOpen ? ' on' : ''}`}
        title={terminalOpen ? '收起终端' : '打开终端'}
        aria-label={terminalOpen ? '收起终端' : '打开终端'}
        aria-pressed={terminalOpen}
        onClick={onToggleTerminal}
        type="button"
      >
        <Icon name="terminal" size={12} />
        终端
      </button>
    </div>
  )
}

function keyOf(tab: EditorTab): string {
  return tab.kind === 'file' ? `file:${tab.path}` : `note:${tab.note.id}`
}

function titleOf(tab: EditorTab): string {
  return tab.kind === 'file' ? baseName(tab.path) : tab.note.title
}

/** Full path or note origin, shown on hover. */
function hintOf(tab: EditorTab): string {
  if (tab.kind === 'file') return tab.path
  return tab.note.project_name ? `${tab.note.title} · ${tab.note.project_name}` : tab.note.title
}
