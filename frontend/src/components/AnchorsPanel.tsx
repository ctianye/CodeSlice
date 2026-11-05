import type { ReactNode } from 'react'
import { useState } from 'react'
import * as api from '../api'
import type { Anchor, AnchorStatus } from '../types'
import { useAsync } from '../hooks/useAsync'
import { baseName } from '../lib/citations'
import { formatRelative } from '../lib/format'
import { useApp } from '../state/AppState'
import { EmptyState, ErrorNote, Loading } from './Feedback'
import { Icon } from './Icon'

const STATUS_LABEL: Record<AnchorStatus, string> = {
  unchanged: '未变化',
  modified: '已修改',
  moved: '已移动',
  deleted: '已删除',
}

const STATUS_CLASS: Record<AnchorStatus, string> = {
  unchanged: 'badge ok',
  modified: 'badge warn',
  moved: 'badge info',
  deleted: 'badge err',
}

/** Code anchors recorded by conversations, with a manual refresh trigger. */
export function AnchorsPanel(): ReactNode {
  const { project, reveal, refreshKey, bumpRefresh } = useApp()
  const projectId = project?.id ?? null
  const anchors = useAsync(
    (signal) => (projectId ? api.listAnchors(projectId, signal) : Promise.resolve<Anchor[]>([])),
    [projectId, refreshKey],
    { enabled: projectId !== null },
  )
  const [refreshing, setRefreshing] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  if (projectId === null) {
    return <EmptyState icon="anchor" title="还没有打开项目" hint="先添加一个本地代码目录，锚点会记录在这里。" />
  }

  const refresh = async () => {
    setRefreshing(true)
    setActionError(null)
    setMessage(null)
    try {
      const result = await api.refreshAnchors(projectId)
      setMessage(`已更新 ${result.updated} 个，移动 ${result.moved} 个，失效 ${result.deleted} 个。`)
      bumpRefresh()
      anchors.reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setRefreshing(false)
    }
  }

  const items = anchors.data ?? []

  return (
    <div className="sidebar-section">
      <div className="sidebar-toolbar">
        <button
          type="button"
          className="small"
          onClick={() => void refresh()}
          disabled={refreshing}
          title="对照当前代码重新校验所有锚点"
        >
          <Icon name="refresh" size={12} />
          {refreshing ? '检查中…' : '重新校验'}
        </button>
        <span className="spacer" />
        <span className="tag" title="锚点数量">
          {items.length}
        </span>
      </div>

      <div className="sidebar-scroll">
        {message ? (
          <div className="row" style={{ padding: '0 10px 6px' }}>
            <span className="muted" style={{ fontSize: 10.5 }}>
              <Icon name="info" size={11} /> {message}
            </span>
          </div>
        ) : null}

        {actionError ? <ErrorNote message={actionError} /> : null}
        {anchors.loading && !anchors.data ? <Loading /> : null}
        {anchors.error ? <ErrorNote message={anchors.error} onRetry={anchors.reload} /> : null}

        {!anchors.loading && !anchors.error && items.length === 0 ? (
          <EmptyState
            icon="anchor"
            title="暂无代码锚点"
            hint="对话中引用过的代码会自动记录在这里，方便确认它是否已被改动。"
          />
        ) : null}

        {items.map((anchor) => (
          <button
            type="button"
            className="anchor-row"
            key={anchor.id}
            onClick={() => reveal(anchor.file_path, anchor.start_line, anchor.end_line)}
            title={`${anchor.file_path} 第 ${anchor.start_line}-${anchor.end_line} 行`}
          >
            <span
              style={{ display: 'flex', flex: '0 0 13px', color: 'var(--text-faint)', marginTop: 2 }}
            >
              <Icon name="anchor" size={13} />
            </span>

            <div className="anchor-body">
              <div className="anchor-path">
                <Icon name="file-code" size={11} />
                <span className="anchor-name">{baseName(anchor.file_path)}</span>
                <span className="anchor-dir muted">· {anchor.file_path}</span>
              </div>
              <div className="anchor-lines">
                L{anchor.start_line}-{anchor.end_line}
                {anchor.symbol ? ` · ${anchor.symbol}` : ''}
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3, flex: '0 0 auto' }}>
              <span className={STATUS_CLASS[anchor.status]}>{STATUS_LABEL[anchor.status]}</span>
              {anchor.checked_at ? (
                <span className="muted" style={{ fontSize: 10 }}>
                  {formatRelative(anchor.checked_at)}
                </span>
              ) : null}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
