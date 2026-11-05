import type { ReactNode } from 'react'
import { useApp } from '../state/AppState'
import { shortenPath } from '../lib/format'

interface TopBarProps {
  onOpenSettings: () => void
  onOpenWelcome: () => void
}

export function TopBar({ onOpenSettings, onOpenWelcome }: TopBarProps): ReactNode {
  const { project } = useApp()

  return (
    <header className="topbar">
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
    </header>
  )
}
