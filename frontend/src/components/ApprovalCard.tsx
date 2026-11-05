import type { ReactNode } from 'react'
import { useState } from 'react'
import type { ApprovalDecision, Interrupt } from '../types'
import { Icon } from './Icon'

interface ApprovalCardProps {
  interrupt: Interrupt
  busy: boolean
  onDecide: (decision: ApprovalDecision) => void
}

const KIND_LABEL: Record<Interrupt['kind'], string> = {
  write_file: '写入文件',
  run_command: '执行命令',
}

interface Decision {
  decision: ApprovalDecision
  label: string
  description: string
}

/** Shown as the two primary buttons. */
const PRIMARY: Decision[] = [
  { decision: 'approve', label: '本次允许', description: '只放行这一次' },
  { decision: 'deny', label: '拒绝', description: '阻止本次操作' },
]

/** Broader grants, visually de-emphasised so the safe choice stays obvious. */
const BROADER: Decision[] = [
  { decision: 'approve_session', label: '本次会话允许', description: '本次对话内同模式不再询问' },
  { decision: 'approve_always', label: '总是允许', description: '为该项目永久记住该模式' },
]

/** Approval gate for write_file / run_command tool calls. */
export function ApprovalCard({ interrupt, busy, onDecide }: ApprovalCardProps): ReactNode {
  const [pending, setPending] = useState<ApprovalDecision | null>(null)
  const { payload } = interrupt

  const decide = (decision: ApprovalDecision) => {
    setPending(decision)
    onDecide(decision)
  }

  return (
    <div className="approval-card">
      <div className="ac-head">
        <span className="ac-icon">
          <Icon name="shield" size={14} />
        </span>
        <span>需要你的确认</span>
        <span className="ac-kind">{KIND_LABEL[interrupt.kind] ?? interrupt.kind}</span>
      </div>

      <div className="ac-reason">{payload.reason}</div>

      {payload.path ? (
        <div className="ac-path">
          <Icon name="file-code" size={11} />
          {payload.path}
        </div>
      ) : null}
      {payload.cwd ? (
        <div className="ac-cwd">
          <Icon name="folder" size={11} />
          工作目录：{payload.cwd}
        </div>
      ) : null}

      {payload.diff ? <DiffBlock diff={payload.diff} /> : null}
      {payload.command ? (
        <pre className="command">
          <span className="muted">$ </span>
          {payload.command}
        </pre>
      ) : null}

      <div className="approval-actions">
        {PRIMARY.map((item) => (
          <button
            key={item.decision}
            className={item.decision === 'deny' ? 'deny' : 'approve'}
            title={item.description}
            disabled={busy || pending !== null}
            onClick={() => decide(item.decision)}
            type="button"
          >
            <Icon name={item.decision === 'deny' ? 'x' : 'check'} size={12} />
            {pending === item.decision ? '处理中…' : item.label}
          </button>
        ))}
      </div>

      <div className="approval-broader">
        <span className="muted">减少打扰</span>
        {BROADER.map((item) => (
          <button
            key={item.decision}
            className="ghost small"
            title={item.description}
            disabled={busy || pending !== null}
            onClick={() => decide(item.decision)}
            type="button"
          >
            {pending === item.decision ? '处理中…' : item.label}
          </button>
        ))}
      </div>
    </div>
  )
}

/** Renders a unified diff with per-line colouring. */
function DiffBlock({ diff }: { diff: string }): ReactNode {
  const lines = diff.split('\n')
  return (
    <pre className="diff">
      {lines.map((line, index) => {
        let cls = 'diff-line'
        if (line.startsWith('+') && !line.startsWith('+++')) cls += ' add'
        else if (line.startsWith('-') && !line.startsWith('---')) cls += ' del'
        else if (line.startsWith('@@')) cls += ' hunk'
        return (
          <div className={cls} key={index}>
            {line || ' '}
          </div>
        )
      })}
    </pre>
  )
}
