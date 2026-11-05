import type { ReactNode } from 'react'
import { useState } from 'react'
import * as api from '../api'
import type { Project } from '../types'
import { isDesktop, hasToken, pickFolder, setDevToken } from '../bridge'
import { useAsync } from '../hooks/useAsync'
import { formatRelative, shortenPath } from '../lib/format'
import { useApp } from '../state/AppState'
import { ErrorNote, Loading } from './Feedback'
import { Icon } from './Icon'

/**
 * First-run / project-picker screen. Picks a folder through the Electron dialog
 * when available, otherwise the user types or pastes a local path.
 */
export function WelcomePage({ onProjectOpened }: { onProjectOpened: () => void }): ReactNode {
  const { projects, setProjects, setProject } = useApp()
  const list = useAsync(() => api.listProjects(), [])

  const [path, setPath] = useState('')
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [token, setToken] = useState('')

  const known = projects.length > 0 ? projects : (list.data ?? [])

  const open = (project: Project) => {
    setProject(project)
    onProjectOpened()
  }

  const choose = async () => {
    const picked = await pickFolder()
    if (picked) setPath(picked)
  }

  const add = async () => {
    const trimmed = path.trim()
    if (trimmed === '') {
      setError('请先填写项目目录的绝对路径。')
      return
    }
    setAdding(true)
    setError(null)
    try {
      const project = await api.createProject({ path: trimmed })
      const next = await api.listProjects()
      setProjects(next)
      setPath('')
      open(project)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setAdding(false)
    }
  }

  return (
    <div className="welcome">
      <div className="welcome-inner">
        <div className="welcome-hero">
          <span className="welcome-eyebrow">
            <Icon name="shield" size={11} />
            本地优先 · 数据不出本机
          </span>
          <h1>
            把代码读成<span className="grad">一段一段</span>的样子
          </h1>
          <p className="lede">
            CodeSlice 把项目按函数与类切成可读的小段，每段配一个独立对话。选中几行就能单独追问，
            回答里带可点击的代码引用。用自己的 API Key，索引与对话都留在本机。
          </p>
        </div>

        <div className="add-project-card">
          <div className="card-title">
            <Icon name="folder-open" size={13} />
            添加一个本地项目
            <span className="spacer" />
            <span className="muted">支持 40+ 种语言</span>
          </div>

          <div className="row">
            <input
              placeholder="D:\\path\\to\\your\\repo"
              value={path}
              spellCheck={false}
              onChange={(event) => setPath(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void add()
              }}
            />
            {isDesktop ? (
              <button onClick={() => void choose()} disabled={adding} type="button">
                <Icon name="folder" size={12} />
                选择文件夹
              </button>
            ) : null}
            <button
              className="primary"
              onClick={() => void add()}
              disabled={adding || path.trim() === ''}
              type="button"
            >
              {adding ? '添加中…' : '添加项目'}
            </button>
          </div>

          <div className="actions">
            <span className="hint">
              <Icon name="shield" size={10} />{' '}
              {isDesktop
                ? '项目路径只在本机读写，不会上传到任何服务器。'
                : '浏览器模式下无法唤起系统目录选择器，请手动粘贴绝对路径。'}
            </span>
          </div>

          {error ? <ErrorNote message={error} /> : null}

          {!hasToken() ? (
            <div className="token-row">
              <div className="field">
                <label htmlFor="dev-token">会话令牌</label>
                <input
                  id="dev-token"
                  placeholder="后端启动日志里的 session token"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                />
                <span className="hint">
                  {isDesktop
                    ? '开发模式下由你自己启动后端，请填入后端输出的令牌；保存后刷新页面生效。'
                    : '浏览器开发模式使用，保存后刷新页面生效。'}
                </span>
              </div>
              <button
                onClick={() => {
                  setDevToken(token.trim())
                  window.location.reload()
                }}
                disabled={token.trim() === ''}
                type="button"
              >
                保存令牌
              </button>
            </div>
          ) : null}
        </div>

        <div className="section-title">最近项目</div>

        {list.loading && known.length === 0 ? <Loading text="正在读取项目列表…" /> : null}
        {list.error ? <ErrorNote message={list.error} onRetry={list.reload} /> : null}

        {!list.loading && known.length === 0 && !list.error ? (
          <div className="list-empty">
            还没有添加任何项目。选择一个本地代码目录开始吧。
          </div>
        ) : null}

        <div className="project-grid">
          {known.map((project) => (
            <button
              className="project-card"
              key={project.id}
              onClick={() => open(project)}
              title={project.path}
              type="button"
            >
              <span className="name">
                <Icon name="folder" size={13} />
                {project.name}
              </span>
              <span className="path">{shortenPath(project.path, 48)}</span>
              <span className="meta">
                <span className={`badge ${project.indexed_at ? 'ok' : 'mute'}`}>
                  {project.indexed_at ? '已索引' : '未索引'}
                </span>
                <span className="muted">
                  {project.indexed_at
                    ? `索引于 ${formatRelative(project.indexed_at)}`
                    : '尚未建立索引'}
                </span>
              </span>
            </button>
          ))}
        </div>

        <div className="welcome-foot">
          <Icon name="terminal" size={11} />
          <span>
            快捷键：<kbd>Enter</kbd> 发送问题 · <kbd>Ctrl</kbd> + <kbd>F</kbd> 在文件中查找
          </span>
        </div>
      </div>
    </div>
  )
}
