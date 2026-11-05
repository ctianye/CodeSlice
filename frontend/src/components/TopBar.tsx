import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'
import { bridge, isDesktop } from '../bridge'
import { useApp } from '../state/AppState'
import { shortenPath } from '../lib/format'

interface TopBarProps {
  onOpenSettings: () => void
  onOpenWelcome: () => void
}

export function TopBar({ onOpenSettings, onOpenWelcome }: TopBarProps): ReactNode {
  const { project } = useApp()
  const [maximized, setMaximized] = useState(() => bridge?.isMaximized() ?? false)

  useEffect(() => bridge?.onWindowState(setMaximized), [])

  return (
    <header className="topbar" onDoubleClick={(event) => {
      if (isDesktop && (event.target as HTMLElement).closest('.window-controls')) return
      if (isDesktop) void bridge?.toggleMaximize().then(setMaximized)
    }}>
      <div className="brand">
        <span className="dot" />
        CodeSlice
      </div>

      {project ? (
        <div className="project-chip" title={project.path}>
          <span className="name">{project.name}</span>
          <span className="path">{shortenPath(project.path, 30)}</span>
        </div>
      ) : null}

      <div className="spacer" />

      <div className="topbar-actions">
        {project ? (
          <button className="ghost small" onClick={onOpenWelcome}>
            切换项目
          </button>
        ) : null}
        <button className="ghost small" onClick={onOpenSettings}>
          设置
        </button>
      </div>
      {isDesktop ? (
        <div className="window-controls" aria-label="窗口控制">
          <button className="window-control" title="最小化" aria-label="最小化" onClick={() => void bridge?.minimize()}>−</button>
          <button className="window-control" title={maximized ? '还原' : '最大化'} aria-label={maximized ? '还原' : '最大化'} onClick={() => void bridge?.toggleMaximize().then(setMaximized)}>{maximized ? '❐' : '□'}</button>
          <button className="window-control close" title="关闭" aria-label="关闭" onClick={() => void bridge?.closeWindow()}>×</button>
        </div>
      ) : null}
    </header>
  )
}
