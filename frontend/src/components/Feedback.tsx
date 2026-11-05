import type { ReactNode } from 'react'
import { Icon } from './Icon'

/** Neutral placeholder for a pane with nothing in it yet. */
export function EmptyState({
  title,
  hint,
  icon = 'sparkles',
  children,
}: {
  title: string
  hint?: string
  icon?: Parameters<typeof Icon>[0]['name']
  children?: ReactNode
}): ReactNode {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon name={icon} size={16} />
      </span>
      <span className="empty-title">{title}</span>
      {hint ? <span className="empty-hint">{hint}</span> : null}
      {children}
    </div>
  )
}

export function Loading({ text = '加载中…' }: { text?: string }): ReactNode {
  return (
    <div className="loading-line">
      <span className="spinner" />
      {text}
    </div>
  )
}

export function ErrorNote({
  message,
  onRetry,
}: {
  message: string
  onRetry?: () => void
}): ReactNode {
  return (
    <div className="error-note" role="alert">
      <span className="err-icon">
        <Icon name="alert" size={13} />
      </span>
      <span className="err-body">{message}</span>
      {onRetry ? (
        <button onClick={onRetry} type="button">
          重试
        </button>
      ) : null}
    </div>
  )
}
