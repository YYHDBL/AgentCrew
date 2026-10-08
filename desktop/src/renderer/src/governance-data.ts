import { useEffect, useState } from 'react'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { api, connection } from './session'
import { parseEventFrame } from './api/events'
import type { Identity, AgentSpec } from './api/types'
export type { Identity, AgentSpec } from './api/types'

export interface Resource { id: string; name: string; status: string; revision: number; workspace_id?: string; data_dir?: string; spec?: AgentSpec; description?: string; source?: string; current_version_id?: string; type?: string; config?: Record<string, unknown>; credential_configured?: boolean; created_at: string }
export interface Grant { id: string; resource_type: string; resource_id: string; grantee_type: string; grantee_id: string; revision: number; revoked_at: string | null; created_at: string; granted_by_user_id: string; workspace_id: string }
export interface Rule { id: string; agent_id: string; tool_name: string; pattern: string; effect: string; revision: number; revoked_at: string | null; created_by_user_id: string }
export interface Membership { id: string; user_id: string; name: string; role: Identity['role']; status: string; revision: number; workspace_ids: string[]; workspace_access: { workspace_id: string; enabled: boolean; revision: number }[] }
export interface Version { id: string; skill_id: string; version_no: number; ledger_id: number; content: string; metadata: { name: string; description: string }; files: { path: string; content: string | null }[]; sha256: string; created_by: string; created_at: string }
export interface AuditRow { seq: number; ts: string; actor_type: string; actor_id: string; action: string; resource_type: string; resource_id: string; detail: Record<string, unknown>; hash: string }
export interface AuditVerification { ok: boolean; internal: { ok: boolean; checked_rows: number; broken_at: number | null; reason: string | null }; anchor: { ok: boolean; status: string; seq: number | null; hash: string | null }; reason?: string }
export interface Backup { id: string; kind: string; verified: boolean; created_at: string; database_sha256: string; anchor_seq: number; anchor_hash: string }
export interface Diagnostic { mode: 'normal' | 'diagnostic'; reason?: string; hint?: string }
export interface Page<T> { items: T[]; next_after: string | null }

export async function allPages<T>(path: string, signal?: AbortSignal): Promise<T[]> {
  let after: string | null = null
  const items: T[] = []
  const used = new Set<string>()
  do {
    const [endpoint, existing] = path.split('?')
    const query = new URLSearchParams(existing)
    query.set('limit', '200')
    if (after) query.set('after', after)
    const page: Page<T> = await api<Page<T>>(`${endpoint}?${query}`, undefined, signal)
    items.push(...page.items)
    after = page.next_after
    if (after && used.has(after)) throw new Error('分页游标重复，停止读取')
    if (after) used.add(after)
  } while (after)
  return items
}

export function useGovernanceEvents(enabled: boolean): { revision: number; status: string; error: string } {
  const [revision, setRevision] = useState(0)
  const [status, setStatus] = useState('正在连接')
  const [error, setError] = useState('')
  useEffect(() => {
    if (!enabled) return
    const lifetime = new AbortController()
    let subscription: AbortController | undefined
    let timer: ReturnType<typeof setTimeout> | undefined
    let cursor = 0
    const run = async (): Promise<void> => {
      try {
        const current = await connection()
        subscription = new AbortController()
        await fetchEventSource(`${current.url}/api/governance/stream?from=${cursor}`, {
          headers: current.headers, signal: subscription.signal, openWhenHidden: true,
          async onopen(response) {
            if (!response.ok || !response.headers.get('content-type')?.startsWith('text/event-stream')) throw new Error(`治理事件订阅失败（${response.status}）`)
            if (!lifetime.signal.aborted) { setStatus('已连接'); setError('') }
          },
          onmessage(message) {
            if (lifetime.signal.aborted || !message.data || message.event === 'ping') return
            if (message.event === 'shutdown' || message.event === 'resync') { cursor = 0; subscription?.abort(); setRevision((value) => value + 1); return }
            const event = parseEventFrame(message.data)
            if (event === null) return
            if (!Number.isSafeInteger(event.global_seq)) throw new Error('治理事件水位无效')
            if (event.global_seq <= cursor) return
            cursor = event.global_seq
            setRevision((value) => value + 1)
          },
          onerror(reason) { throw reason },
          onclose() { throw new Error('治理事件连接已关闭') }
        })
      } catch (reason) {
        if (!lifetime.signal.aborted) { setStatus('正在重新连接'); setError(reason instanceof Error ? reason.message : String(reason)); setRevision((value) => value + 1) }
      }
      if (!lifetime.signal.aborted) timer = setTimeout(() => void run(), 1000)
    }
    void run()
    return () => { lifetime.abort(); subscription?.abort(); clearTimeout(timer) }
  }, [enabled])
  return { revision, status, error }
}
