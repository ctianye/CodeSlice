import type { ReactNode } from 'react'
import { useState } from 'react'
import * as api from '../api'
import type { CommandRule, RuleScope } from '../types'
import { useAsync } from '../hooks/useAsync'
import { useApp } from '../state/AppState'
import { ErrorNote, Loading } from './Feedback'

const SCOPE_LABEL: Record<RuleScope, string> = {
  once: '一次',
  session: '本会话',
  always: '始终',
}

/** CRUD for per-project command approval rules. */
export function CommandRulesPanel(): ReactNode {
  const { project } = useApp()
  const projectId = project?.id ?? null
  const rules = useAsync(
    (signal) => (projectId ? api.listCommandRules(projectId, signal) : Promise.resolve<CommandRule[]>([])),
    [projectId],
    { enabled: projectId !== null },
  )

  const [pattern, setPattern] = useState('')
  const [scope, setScope] = useState<RuleScope>('session')
  const [needsConfirm, setNeedsConfirm] = useState(true)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  if (projectId === null) return <div className="list-empty">请先选择项目。</div>

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

  return (
    <div>
      <div className="section-label">命令审批规则</div>
      <p style={{ margin: '0 0 10px', fontSize: 11, color: 'var(--text-faint)' }}>
        匹配的命令在 Agent 执行前会触发审批。模式支持通配符，例如 <code>pytest *</code>。
      </p>

      {actionError ? <ErrorNote message={actionError} /> : null}
      {rules.loading && !rules.data ? <Loading /> : null}
      {rules.error ? <ErrorNote message={rules.error} onRetry={rules.reload} /> : null}
      {rules.data && rules.data.length === 0 ? (
        <div className="list-empty">还没有命令规则。</div>
      ) : null}

      {(rules.data ?? []).map((rule) => (
        <div className="rule-row" key={rule.id}>
          <span className="pattern" title={rule.pattern}>
            {rule.pattern}
          </span>
          <select
            value={rule.scope}
            onChange={(event) => void change(rule, { scope: event.target.value as RuleScope })}
          >
            {(Object.keys(SCOPE_LABEL) as RuleScope[]).map((key) => (
              <option key={key} value={key}>
                {SCOPE_LABEL[key]}
              </option>
            ))}
          </select>
          <label style={{ fontSize: 10.5, color: 'var(--text-faint)', display: 'flex', gap: 4 }}>
            <input
              type="checkbox"
              style={{ width: 'auto' }}
              checked={rule.needs_confirm}
              onChange={(event) => void change(rule, { needs_confirm: event.target.checked })}
            />
            需确认
          </label>
          <button className="small ghost danger" onClick={() => void remove(rule)}>
            删
          </button>
        </div>
      ))}

      <div className="rule-form">
        <input
          placeholder="命令模式，例如 pytest *"
          value={pattern}
          onChange={(event) => setPattern(event.target.value)}
        />
        <select value={scope} onChange={(event) => setScope(event.target.value as RuleScope)}>
          {(Object.keys(SCOPE_LABEL) as RuleScope[]).map((key) => (
            <option key={key} value={key}>
              {SCOPE_LABEL[key]}
            </option>
          ))}
        </select>
        <label style={{ fontSize: 10.5, color: 'var(--text-faint)', display: 'flex', gap: 4 }}>
          <input
            type="checkbox"
            style={{ width: 'auto' }}
            checked={needsConfirm}
            onChange={(event) => setNeedsConfirm(event.target.checked)}
          />
          需确认
        </label>
        <button className="small primary" onClick={() => void add()} disabled={saving || pattern.trim() === ''}>
          {saving ? '保存中…' : '添加'}
        </button>
      </div>
    </div>
  )
}
