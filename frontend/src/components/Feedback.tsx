import type { ReactNode } from 'react'

interface EmptyStateProps {
  title: string
  hint?: string
  children?: ReactNode
}

export function EmptyState({ title, hint, children }: EmptyStateProps): ReactNode {
  return (
    <div className="empty-state">
      <p className="empty-title">{title}</p>
      {hint ? <p className="empty-hint">{hint}</p> : null}
      {children}
    </div>
  )
}

export function Loading({ text = '加载中…' }: { text?: string }): ReactNode {
  return <div className="loading-line">{text}</div>
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }): ReactNode {
  return (
    <div className="error-note">
      <span>{message}</span>
      {onRetry ? (
        <button className="small ghost" onClick={onRetry}>
          重试
        </button>
      ) : null}
    </div>
  )
}
