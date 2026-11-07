import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'
import * as api from '../api'
import type { ModelConfig, ModelProtocol, ModelSettings, ModelTestResult } from '../types'
import { usePersistedState } from '../hooks/useAsync'
import { useApp } from '../state/AppState'
import type { AppTheme } from '../state/AppState'
import { CommandRulesPanel } from './CommandRulesPanel'
import { ErrorNote, Loading } from './Feedback'
import { Icon, Logo } from './Icon'
import type { IconName } from './Icon'
import { VectorModelsPanel } from './VectorModelsPanel'

type Tab = 'model' | 'rules' | 'appearance' | 'about'

interface SettingsModalProps {
  onClose: () => void
}

const TABS: Array<{ id: Tab; label: string; icon: IconName }> = [
  { id: 'model', label: '模型', icon: 'cpu' },
  { id: 'rules', label: '命令规则', icon: 'terminal' },
  { id: 'appearance', label: '外观', icon: 'sun' },
  { id: 'about', label: '关于', icon: 'info' },
]

/** Settings dialog: model configuration, command rules, and build info. */
export function SettingsModal({ onClose }: SettingsModalProps): ReactNode {
  const [tab, setTab] = usePersistedState<Tab>('codeslice.settingsTab', 'model')

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="设置"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <span className="modal-icon">
            <Icon name="settings" size={14} />
          </span>
          <h2>设置</h2>
          <button className="ghost icon" onClick={onClose} title="关闭（Esc）" aria-label="关闭" type="button">
            <Icon name="x" size={14} />
          </button>
        </div>

        <div className="modal-tabs">
          {TABS.map((item) => (
            <button
              key={item.id}
              className={tab === item.id ? 'active' : ''}
              onClick={() => setTab(item.id)}
              type="button"
            >
              <Icon name={item.icon} size={12} />
              {item.label}
            </button>
          ))}
        </div>

        <div className="modal-body">
          {tab === 'model' ? <ModelSettingsForm /> : null}
          {tab === 'rules' ? <CommandRulesPanel /> : null}
          {tab === 'appearance' ? <AppearancePanel /> : null}
          {tab === 'about' ? <AboutPanel /> : null}
        </div>
      </div>
    </div>
  )
}

function AppearancePanel(): ReactNode {
  const { theme, setTheme } = useApp()
  const options: Array<{ value: AppTheme; label: string; hint: string }> = [
    { value: 'dark', label: '深色', hint: '长时间读代码更省眼' },
    { value: 'light', label: '浅色', hint: '明亮环境、投影分享' },
  ]

  return (
    <div>
      <div className="section-label">界面主题</div>
      <div className="theme-options" role="group" aria-label="界面主题">
        {options.map((item) => (
          <button
            key={item.value}
            className={`theme-option${theme === item.value ? ' active' : ''}`}
            aria-pressed={theme === item.value}
            onClick={() => setTheme(item.value)}
            type="button"
          >
            <span className={`theme-swatch ${item.value}`} />
            <span className="theme-label">
              <strong>{item.label}</strong>
              <span>{item.hint}</span>
            </span>
            {theme === item.value ? <span className="spacer" /> : null}
            {theme === item.value ? <Icon name="check" size={13} /> : null}
          </button>
        ))}
      </div>
    </div>
  )
}

function ModelSettingsForm(): ReactNode {
  const [settings, setSettings] = useState<ModelSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<ModelTestResult | null>(null)

  const [apiKey, setApiKey] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .getModelSettings()
      .then((next) => {
        if (cancelled) return
        setSettings(next)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setLoadError(err instanceof Error ? err.message : String(err))
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (loading) return <Loading text="正在读取模型配置…" />
  if (loadError) return <ErrorNote message={loadError} />
  if (!settings) return <ErrorNote message="未能读取模型配置。" />

  const chat = settings.chat

  const patchChat = (patch: Partial<ModelConfig>) => {
    setSaveMessage(null)
    setSettings((prev) => (prev ? { ...prev, chat: { ...prev.chat, ...patch } } : prev))
  }

  const save = async () => {
    setSaving(true)
    setActionError(null)
    setSaveMessage(null)
    try {
      // Only chat is sent here; the vector-model panel owns `embedding` and
      // writes it itself, so echoing a stale copy back would undo a switch.
      const body = {
        chat: {
          protocol: chat.protocol,
          model: chat.model,
          base_url: chat.base_url,
          temperature: chat.temperature,
          max_tokens: chat.max_tokens,
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        },
      }
      const next = await api.updateModelSettings(body)
      setSettings(next)
      setApiKey('')
      setSaveMessage('已保存。')
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setActionError(null)
    setTestResult(null)
    try {
      setTestResult(await api.testModel())
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setTesting(false)
    }
  }

  const clearKey = async () => {
    if (!window.confirm('确定要清除本机保存的 API Key 吗？')) return
    setClearing(true)
    setActionError(null)
    setSaveMessage(null)
    try {
      await api.clearModelKey('chat')
      setSettings(await api.getModelSettings())
      setApiKey('')
      setSaveMessage('已清除保存的密钥。')
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setClearing(false)
    }
  }

  return (
    <div>
      <div className="section-label">对话模型</div>

      <div className="form-grid">
        <div className="field">
          <label htmlFor="protocol">协议</label>
          <select
            id="protocol"
            value={chat.protocol}
            onChange={(event) => patchChat({ protocol: event.target.value as ModelProtocol })}
          >
            <option value="openai-compatible">OpenAI 兼容</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </div>

        <div className="field">
          <label htmlFor="model">模型名称</label>
          <input
            id="model"
            placeholder="例如 deepseek-chat"
            value={chat.model}
            onChange={(event) => patchChat({ model: event.target.value })}
          />
        </div>

        <div className="field full">
          <label htmlFor="base-url">接口地址（可选）</label>
          <input
            id="base-url"
            placeholder="留空使用服务商默认地址"
            value={chat.base_url ?? ''}
            onChange={(event) => patchChat({ base_url: event.target.value || null })}
          />
          <span className="hint">本地代理或自建网关时填写完整的 API Base URL。</span>
        </div>

        <div className="field">
          <label htmlFor="temperature">温度</label>
          <input
            id="temperature"
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={chat.temperature}
            onChange={(event) => patchChat({ temperature: Number(event.target.value) })}
          />
        </div>

        <div className="field">
          <label htmlFor="max-tokens">最大输出长度</label>
          <input
            id="max-tokens"
            type="number"
            min={256}
            max={32000}
            step={256}
            value={chat.max_tokens}
            onChange={(event) => patchChat({ max_tokens: Number(event.target.value) })}
          />
          <span className="hint">单次回答允许生成的最大 token 数。</span>
        </div>

        <div className="field full">
          <label htmlFor="api-key">
            API Key
            {chat.api_key_configured ? (
              <span className="configured">
                <Icon name="check" size={10} />
                已配置
              </span>
            ) : null}
          </label>
          <input
            id="api-key"
            type="password"
            autoComplete="off"
            placeholder={chat.api_key_configured ? '已保存，留空表示不修改' : '填入你的 API Key'}
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
          />
          <span className="hint">密钥仅写入本地系统钥匙串，接口不会回显明文。</span>
          {chat.api_key_configured ? (
            <div className="row">
              <button
                className="ghost small"
                onClick={() => void clearKey()}
                disabled={clearing}
                title="删除本机保存的 API Key"
                type="button"
              >
                <Icon name="trash" size={11} />
                {clearing ? '清除中…' : '清除已保存的密钥'}
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {actionError ? <ErrorNote message={actionError} /> : null}
      {saveMessage ? (
        <div className="test-result ok">
          <Icon name="check" size={12} />
          {saveMessage}
        </div>
      ) : null}
      {testResult ? (
        <div className={`test-result ${testResult.ok ? 'ok' : 'err'}`}>
          <Icon name={testResult.ok ? 'check' : 'alert'} size={12} />
          <span>
            {testResult.ok ? '连通性正常' : '连接失败'}
            {testResult.latency_ms !== null ? ` · ${testResult.latency_ms} ms` : ''}
            {testResult.detail ? ` · ${testResult.detail}` : ''}
          </span>
        </div>
      ) : null}

      <div className="form-actions">
        <button
          className="primary"
          onClick={() => void save()}
          disabled={saving || chat.model.trim() === ''}
          type="button"
        >
          <Icon name="check" size={12} />
          {saving ? '保存中…' : '保存配置'}
        </button>
        <button onClick={() => void test()} disabled={testing} type="button">
          <Icon name="refresh" size={12} />
          {testing ? '测试中…' : '测试连接'}
        </button>
        <span className="grow">
          {settings.embedding
            ? `向量模型：${settings.embedding.model}`
            : '未配置向量模型，检索将退回关键词模式'}
        </span>
      </div>

      <div className="settings-divider" />

      <VectorModelsPanel
        embedding={settings.embedding}
        onChanged={(next) => {
          setSettings(next)
          setSaveMessage(null)
        }}
      />
    </div>
  )
}

function AboutPanel(): ReactNode {
  const [health, setHealth] = useState<{ version: string; data_dir: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { project } = useApp()

  useEffect(() => {
    let cancelled = false
    api
      .getHealth()
      .then((next) => {
        if (!cancelled) setHealth(next)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div>
      <div className="about-brand" style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 12, fontWeight: 650 }}>
        <Logo size={26} />
        <span>CodeSlice</span>
      </div>
      <div className="section-label">关于 CodeSlice</div>
      <p className="lede" style={{ fontSize: 11.5, marginBottom: 14 }}>
        本地优先的代码阅读助手。所有代码与对话数据都保存在本机，模型请求直接由你配置的服务商处理。
      </p>

      {error ? <ErrorNote message={error} /> : null}
      {health ? (
        <dl className="about-grid">
          <dt>后端版本</dt>
          <dd>{health.version}</dd>
          <dt>数据目录</dt>
          <dd>{health.data_dir}</dd>
          <dt>当前项目</dt>
          <dd>{project ? `${project.name}（${project.path}）` : '未选择'}</dd>
        </dl>
      ) : null}

      <div className="section-label">交互提示</div>
      <div className="shortcut-list">
        <div className="shortcut">
          <span className="keys">
            <kbd>拖选</kbd>
          </span>
          <span>在编辑器中选中代码行，点「就这段提问」创建片段对话。</span>
        </div>
        <div className="shortcut">
          <span className="keys">
            <kbd>Enter</kbd>
          </span>
          <span>发送问题；<kbd>Shift</kbd> + <kbd>Enter</kbd> 换行。</span>
        </div>
        <div className="shortcut">
          <span className="keys">
            <kbd>Esc</kbd>
          </span>
          <span>关闭当前设置弹窗。</span>
        </div>
        <div className="shortcut">
          <span className="keys">
            <Icon name="shield" size={12} />
          </span>
          <span>涉及写文件或执行命令时，Agent 会暂停并等待你确认。</span>
        </div>
      </div>
    </div>
  )
}
