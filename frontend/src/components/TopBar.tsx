import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'
import { bridge, isDesktop } from '../bridge'
import { useApp } from '../state/AppState'
import { shortenPath } from '../lib/format'
import { Icon, Logo } from './Icon'

interface TopBarProps {
  onOpenSettings: () => void
  onOpenWelcome: () => void
}

export function TopBar({ onOpenSettings, onOpenWelcome }: TopBarProps): ReactNode {
  const { project, theme, setTheme } = useApp()
  const [maximized, setMaximized] = useState(() => bridge?.isMaximized() ?? false)

  useEffect(() => bridge?.onWindowState(setMaximized), [])

  return (
    <header
      className="topbar"
      onDoubleClick={(event) => {
        if (isDesktop && (event.target as HTMLElement).closest('.window-controls')) return
        if (isDesktop) void bridge?.toggleMaximize().then(setMaximized)
      }}
    >
      <div className="brand">
        <span className="logo">
          <Logo size={17} />
        </span>
        CodeSlice
      </div>

      {project ? (
        <>
          <span className="topbar-sep" />
          <div className="project-chip" title={project.path}>
            <span className="name">{project.name}</span>
            <span className="path">{shortenPath(project.path, 34)}</span>
          </div>
        </>
      ) : null}

      <div className="spacer" />

      <div className="topbar-actions">
        <button
          className="ghost icon"
          title={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
          aria-label={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          type="button"
        >
          <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={14} />
        </button>
        {project ? (
          <button className="ghost small" onClick={onOpenWelcome} type="button">
            <Icon name="folder-open" size={12} />
            切换项目
          </button>
        ) : null}
        <button className="ghost small" onClick={onOpenSettings} type="button">
          <Icon name="settings" size={12} />
          设置
        </button>
      </div>

      {isDesktop ? (
        <div className="window-controls" aria-label="窗口控制">
          <button
            className="window-control"
            title="最小化"
            aria-label="最小化"
            onClick={() => void bridge?.minimize()}
            type="button"
          >
            <Icon name="minimize" size={14} />
          </button>
          <button
            className="window-control"
            title={maximized ? '还原' : '最大化'}
            aria-label={maximized ? '还原' : '最大化'}
            onClick={() => void bridge?.toggleMaximize().then(setMaximized)}
            type="button"
          >
            <Icon name={maximized ? 'restore' : 'maximize'} size={12} />
          </button>
          <button
            className="window-control close"
            title="关闭"
            aria-label="关闭"
            onClick={() => void bridge?.closeWindow()}
            type="button"
          >
            <Icon name="x" size={14} />
          </button>
        </div>
      ) : null}
    </header>
  )
}
