import type { ReactNode } from 'react'
import { useState } from 'react'
import * as api from '../api'
import type { CommandRule, RuleScope } from '../types'
import { useAsync } from '../hooks/useAsync'
import { useApp } from '../state/AppState'
import { EmptyState, ErrorNote, Loading } from './Feedback'
import { Icon } from './Icon'

const SCOPE_LABEL: Record<RuleScope, string> = {
  once: '一次',
  session: '本会话',
  always: '始终',
}

const SCOPES = Object.keys(SCOPE_LABEL) as RuleScope[]

/** CRUD for per-project command approval rules. */
export function CommandRulesPanel(): ReactNode {
  const { project } = useApp()
  const projectId = project?.id ?? null
  const rules = useAsync(
    (signal) =>
      projectId ? api.listCommandRules(projectId, signal) : Promise.resolve<CommandRule[]>([]),
    [projectId],
    { enabled: projectId !== null },
  )

  const [pattern, setPattern] = useState('')
  const [scope, setScope] = useState<RuleScope>('session')
  const [needsConfirm, setNeedsConfirm] = useState(true)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  if (projectId === null) {
    return (
      <EmptyState icon="terminal" title="还没有打开项目" hint="命令规则按项目保存，请先添加一个项目。" />
    )
  }

  const add = async () => {
    if (pattern.trim() === '') return
    setSaving(true)
    setActionError(null)
    try {
      await api.createCommandRule(projectId, {
        pattern: pattern.trim(),
        scope,
        needs_confirm: needsConfirm,
      })
      setPattern('')
      rules.reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const change = async (rule: CommandRule, patch: { scope?: RuleScope; needs_confirm?: boolean }) => {
    setActionError(null)
    try {
      await api.updateCommandRule(projectId, rule.id, patch)
      rules.reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    }
  }

  const remove = async (rule: CommandRule) => {
    if (!window.confirm(`删除规则「${rule.pattern}」？`)) return
    setActionError(null)
    try {
      await api.deleteCommandRule(projectId, rule.id)
      rules.reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    }
  }

  const items = rules.data ?? []

  return (
    <div>
      <div className="section-label">
        <Icon name="terminal" size={12} />
        命令审批规则
      </div>
      <p className="muted" style={{ margin: '0 0 12px', fontSize: 11 }}>
        匹配的命令在 Agent 执行前会触发审批。模式支持通配符，例如 <code>pytest *</code>。
      </p>

      {actionError ? <ErrorNote message={actionError} /> : null}
      {rules.loading && !rules.data ? <Loading /> : null}
      {rules.error ? <ErrorNote message={rules.error} onRetry={rules.reload} /> : null}

      {!rules.loading && !rules.error && items.length === 0 ? (
        <EmptyState icon="terminal" title="还没有命令规则" hint="没有规则的命令每次执行前都会请求确认。" />
      ) : null}

      {items.length > 0 ? (
        <div className="rule-list">
          {items.map((rule) => (
            <div className="rule-row" key={rule.id}>
              <span className="pattern" title={rule.pattern}>
                {rule.pattern}
              </span>

              <select
                value={rule.scope}
                aria-label={`规则 ${rule.pattern} 的生效范围`}
                onChange={(event) => void change(rule, { scope: event.target.value as RuleScope })}
              >
                {SCOPES.map((key) => (
                  <option key={key} value={key}>
                    {SCOPE_LABEL[key]}
                  </option>
                ))}
              </select>

              <label className="muted" style={{ fontSize: 10.5, display: 'flex', gap: 4 }}>
                <input
                  type="checkbox"
                  checked={rule.needs_confirm}
                  onChange={(event) => void change(rule, { needs_confirm: event.target.checked })}
                />
                需确认
              </label>

              <button
                type="button"
                className="icon danger"
                onClick={() => void remove(rule)}
                title="删除规则"
                aria-label={`删除规则 ${rule.pattern}`}
              >
                <Icon name="trash" size={12} />
              </button>
            </div>
          ))}
        </div>
      ) : null}

      <div className="rule-form">
        <input
          placeholder="命令模式，例如 pytest *"
          aria-label="命令模式"
          value={pattern}
          onChange={(event) => setPattern(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void add()
          }}
        />
        <select
          value={scope}
          aria-label="新规则的生效范围"
          onChange={(event) => setScope(event.target.value as RuleScope)}
        >
          {SCOPES.map((key) => (
            <option key={key} value={key}>
              {SCOPE_LABEL[key]}
            </option>
          ))}
        </select>
        <label className="muted" style={{ fontSize: 10.5, display: 'flex', gap: 4 }}>
          <input
            type="checkbox"
            checked={needsConfirm}
            onChange={(event) => setNeedsConfirm(event.target.checked)}
          />
          需确认
        </label>
        <button
          type="button"
          className="small primary"
          onClick={() => void add()}
          disabled={saving || pattern.trim() === ''}
        >
          <Icon name="plus" size={12} />
          {saving ? '保存中…' : '添加'}
        </button>
      </div>
    </div>
  )
}
