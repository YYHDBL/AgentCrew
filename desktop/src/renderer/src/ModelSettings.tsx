import { useLayoutEffect, useRef, useState } from 'react'
import { Button, Input, Popconfirm, Select } from 'antd'
import { api } from './session'
import type { components } from './api/generated/http'
import type { Identity } from './api/types'

type Settings = components['schemas']['Settings']
type SlotView = components['schemas']['ModelSlot']
type CatalogEntry = components['schemas']['ModelCatalogEntry']
type VerifyData = components['schemas']['ModelVerifyData']
type SlotName = 'main' | 'aux'

interface Draft { provider: string; model: string; base_url: string; api_key: string }

const slotNames: Record<SlotName, string> = { main: '主模型（main）', aux: '辅助模型（aux，摘要与记忆提炼）' }

const emptyDraft = (slot?: SlotView): Draft => ({
  provider: slot?.provider || 'glm', model: slot?.model || '', base_url: slot?.base_url || '', api_key: ''
})

function CountingStatus({ slot }: { slot: SlotView }): JSX.Element {
  if (!slot.model) return <p className="model-counting">未配置模型。</p>
  if (!slot.context_window) return <p className="model-counting">该配置没有经核查的官方计数依据；任务会在发送前被明确拒绝（预算检查不可关闭）。</p>
  return <p className="model-counting">计数依据：窗口 {slot.context_window.toLocaleString()} token（<a href={slot.context_window_source ?? undefined} target="_blank" rel="noreferrer">来源</a>），官方编码 revision <code>{slot.tokenizer_revision?.slice(0, 12)}…</code>，SHA-256 <code>{slot.tokenizer_sha256?.slice(0, 12)}…</code>。</p>
}

function VerifyOutcome({ name, result }: { name: string; result?: VerifyData['results']['main'] }): JSX.Element | null {
  if (!result) return null
  const counting = result.counting.ok
    ? `通过（窗口 ${result.counting.context_window?.toLocaleString()}，${result.counting.counting_method}）`
    : `未通过：${result.counting.error ?? '未知原因'}`
  const request = result.request.ok
    ? `通过（${result.request.elapsed_ms}ms，真实小请求）`
    : `未通过：${result.request.error ?? '未知原因'}`
  return <div className="model-verify-outcome" role="status">
    <h4>{name}</h4>
    <p className={result.counting.ok ? 'verify-ok' : 'verify-failed'}>计数资产：{counting}</p>
    <p className={result.request.ok ? 'verify-ok' : 'verify-failed'}>连接请求：{request}</p>
  </div>
}

export function ModelSettings({ identity, enabled, onSaved }: { identity: Identity | null; enabled: boolean; onSaved: () => void }): JSX.Element {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [drafts, setDrafts] = useState<Record<SlotName, Draft> | null>(null)
  const [verification, setVerification] = useState<VerifyData | null>(null)
  const [saving, setSaving] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const lifetime = useRef<AbortController | null>(null)
  const canEdit = enabled && (identity?.role === 'owner' || identity?.role === 'admin')

  useLayoutEffect(() => {
    setSettings(null); setDrafts(null); setVerification(null)
    setSaving(false); setVerifying(false); setMessage(''); setError('')
    if (!enabled) return
    const controller = new AbortController()
    lifetime.current = controller
    void api<Settings>('/settings', undefined, controller.signal).then((value) => {
      if (controller.signal.aborted) return
      setSettings(value)
      setDrafts({ main: emptyDraft(value.models?.main), aux: emptyDraft(value.models?.aux) })
    }, (reason: Error) => {
      if (controller.signal.aborted) return
      setError(reason.message)
    })
    return () => { controller.abort(); lifetime.current = null }
  }, [enabled, identity?.effective_user_id, identity?.role])

  const update = (slot: SlotName, patch: Partial<Draft>): void => {
    if (!drafts) return
    setDrafts({ ...drafts, [slot]: { ...drafts[slot], ...patch } })
    setMessage('')
  }

  const applyCatalog = (slot: SlotName, entry: CatalogEntry): void => {
    update(slot, { provider: entry.provider, model: entry.model, base_url: entry.base_url })
  }

  const save = async (): Promise<void> => {
    const controller = lifetime.current
    if (!canEdit || !drafts || saving || !controller || controller.signal.aborted) return
    setSaving(true); setMessage(''); setError('')
    const models: Record<string, Record<string, string>> = {}
    for (const slot of ['main', 'aux'] as const) {
      const draft = drafts[slot]
      models[slot] = {
        provider: draft.provider,
        // 输入即所见：模型已设时地址随输入原样提交（空 = 清除自定义地址；
        // glm 空即默认端点，openai-compatible 空会被服务端明确拒绝）
        ...(draft.model ? { model: draft.model, base_url: draft.base_url } : {}),
        ...(draft.api_key ? { api_key: draft.api_key } : {}),
      }
    }
    try {
      const result = await api<{ settings: Settings; ignored_fields: string[] }>('/settings', { models }, controller.signal, 'PATCH')
      if (controller.signal.aborted) return
      setSettings(result.settings)
      setDrafts({ main: emptyDraft(result.settings.models?.main), aux: emptyDraft(result.settings.models?.aux) })
      onSaved()
      const ignored = result.ignored_fields
      setMessage(ignored.length
        ? '配置已保存。以下字段当前被环境变量覆盖，保存只写入文件、不影响生效值：' + ignored.join('、')
        : '配置已保存。可用“验证连接”发起真实检查。')
    } catch (reason) {
      if (controller.signal.aborted) return
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }

  const clearKey = async (slot: SlotName): Promise<void> => {
    const controller = lifetime.current
    if (!canEdit || saving || !controller || controller.signal.aborted) return
    setSaving(true); setMessage(''); setError('')
    try {
      const result = await api<{ settings: Settings }>('/settings', { api_key_clear: [slot] }, controller.signal, 'PATCH')
      if (controller.signal.aborted) return
      setSettings(result.settings)
      onSaved()
      setMessage(`${slotNames[slot]}的密钥已清除。`)
    } catch (reason) {
      if (controller.signal.aborted) return
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      if (!controller.signal.aborted) setSaving(false)
    }
  }

  const verify = async (): Promise<void> => {
    const controller = lifetime.current
    if (!enabled || verifying || !controller || controller.signal.aborted) return
    setVerifying(true); setMessage(''); setError(''); setVerification(null)
    try {
      const result = await api<VerifyData>('/settings/models/verify', { slots: ['main', 'aux'] }, controller.signal, 'POST')
      if (controller.signal.aborted) return
      setVerification(result)
    } catch (reason) {
      if (controller.signal.aborted) return
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setVerifying(false)
    }
  }

  const slotSection = (slot: SlotName): JSX.Element | null => {
    if (!settings || !drafts) return null
    const current: SlotView = settings.models?.[slot]
      ?? { provider: 'glm', model: '', base_url: '', api_key_configured: false, api_key_hint: '', effective_source: 'file', max_tokens: 4096 }
    const draft = drafts[slot]
    return <section className="model-slot" aria-label={slotNames[slot]}>
      <h3>{slotNames[slot]}</h3>
      <p className="model-key-status">{current.api_key_configured
        ? `密钥已配置（尾 4 位 ${current.api_key_hint || '****'}）`
        : '密钥未配置'}</p>
      {current.effective_source === 'env' && <p className="model-env-note">该槽有字段被环境变量覆盖：界面保存只写入配置文件，当前生效值来自环境。</p>}
      <div className="model-fields">
        <label>
          <span>协议</span>
          <Select aria-label={`${slot} 协议`} value={draft.provider} disabled={!canEdit || saving}
            onChange={(value) => update(slot, { provider: value })}
            options={[{ value: 'glm', label: 'GLM（Anthropic Messages）' }, { value: 'openai-compatible', label: 'OpenAI 兼容（Chat Completions）' }]} />
        </label>
        <label>
          <span>模型</span>
          <Input aria-label={`${slot} 模型`} value={draft.model} disabled={!canEdit || saving}
            placeholder="模型标识" onChange={(event) => update(slot, { model: event.target.value })} />
        </label>
        <label>
          <span>请求地址</span>
          <Input aria-label={`${slot} 请求地址`} value={draft.base_url} disabled={!canEdit || saving}
            placeholder={draft.provider === 'glm' ? 'GLM 默认端点' : '例如 https://opencode.ai/zen/go/v1'}
            onChange={(event) => update(slot, { base_url: event.target.value })} />
        </label>
        <label>
          <span>API 密钥</span>
          <Input.Password aria-label={`${slot} API 密钥`} value={draft.api_key} disabled={!canEdit || saving}
            autoComplete="new-password" placeholder={current.api_key_configured ? '留空保持现有密钥' : '输入密钥'}
            onChange={(event) => update(slot, { api_key: event.target.value })} />
        </label>
      </div>
      {canEdit && current.api_key_configured && <Popconfirm title={`清除 ${slotNames[slot]}的密钥？`} okText="确认清除" cancelText="取消" onConfirm={() => void clearKey(slot)}>
        <Button disabled={saving}>清除该槽密钥</Button>
      </Popconfirm>}
      <CountingStatus slot={current} />
    </section>
  }

  return <main className="memory-page model-settings" aria-busy={enabled && !settings && !error}>
    <div className="memory-heading"><div><h1>模型连接</h1><p>配置主模型与辅助模型的协议、端点和密钥。密钥只保存在本机数据目录，不会回显、不会进入日志或应用包。</p></div></div>
    {!enabled && <p role="status">等待任务服务连接后读取配置。</p>}
    {enabled && !settings && !error && <p role="status">正在读取模型配置。</p>}
    {error && <p className="automation-error" role="alert">{error}</p>}
    {message && <p className="automation-note" role="status">{message}</p>}
    {settings && <>
      <section aria-label="已知模型">
        <h2>已知模型（服务端核查）</h2>
        <p>窗口与官方计数依据由服务端元数据提供，不能手工填写哈希声明可用。</p>
        <ul className="model-catalog">
          {(settings.catalog ?? []).map((entry) => <li key={`${entry.provider}:${entry.model}:${entry.base_url}`}>
            <div>
              <strong>{entry.label}</strong>
              <span>{entry.model || '需在下方填写模型标识'} · {entry.base_url}</span>
              <span>{entry.counting_available
                ? `官方计数可用（窗口 ${entry.context_window?.toLocaleString()}）`
                : '官方计数依据尚未核查，暂不可用于任务'}</span>
            </div>
            {canEdit && <span className="model-pick-actions">
              <Button size="small" disabled={saving} onClick={() => applyCatalog('main', entry)}>用于主模型</Button>
              <Button size="small" disabled={saving} onClick={() => applyCatalog('aux', entry)}>用于辅助模型</Button>
            </span>}
          </li>)}
        </ul>
      </section>
      {slotSection('main')}
      {slotSection('aux')}
      <section className="model-actions">
        {!canEdit && <p>当前身份为成员，模型配置只读。</p>}
        {canEdit && saving && <p role="status">正在保存配置…</p>}
        <Button type="primary" disabled={!canEdit || saving} onClick={() => void save()}>保存配置</Button>
        <Button disabled={!enabled || verifying} onClick={() => void verify()} aria-label={verifying ? '正在验证连接' : '验证连接'}>{verifying ? '验证连接中…' : '验证连接'}</Button>
      </section>
      {verification && <section aria-label="验证结果">
        <h2>连接验证结果</h2>
        <p>{verification.note}</p>
        <VerifyOutcome name={slotNames.main} result={verification.results.main} />
        <VerifyOutcome name={slotNames.aux} result={verification.results.aux} />
      </section>}
    </>}
  </main>
}
