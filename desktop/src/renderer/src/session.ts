import { useEffect, useState } from 'react'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { parseEventFrame } from './api/events'
import type { EventFrame } from './api/generated/events'

export interface Conversation { id: string; title: string | null; state_badge: string; last_activity_at: string; agent_name: string; agent_id?: string; workspace_id?: string }
export interface Snapshot {
  state: string; at_global_seq: number; waiting_approvals: number; waiting_questions: number
  queue_paused: boolean; queue: { id: string; text: string }[]
  can_send: boolean; can_queue: boolean; can_cancel: boolean; can_continue_queue: boolean
  current_task_run_id: string | null
}
export type Frame = EventFrame
export interface Scope { workspace_dir: string; materials_dir: string; folders: { path: string; access: string }[] }

let identityGeneration = 0
export function setIdentityToken(token: string): void {
  identityGeneration += 1
  sessionStorage.clear()
  if (token) sessionStorage.setItem('agentcrew-identity', token)
  window.dispatchEvent(new Event('agentcrew-identity-changed'))
}

export async function connection(): Promise<{ url: string; headers: Record<string, string> }> {
  const identity = sessionStorage.getItem('agentcrew-identity')
  const generation = identityGeneration
  const [port, token] = await Promise.all([window.agentcrew.getBackendPort(), window.agentcrew.getToken()])
  if (generation !== identityGeneration) throw new ApiFailure('当前请求身份已更换', 'IDENTITY_CHANGED', 0)
  if (!port || !token) throw new Error('任务服务尚未就绪')
  return { url: `http://127.0.0.1:${port}`, headers: { Authorization: `Bearer ${token}`, ...(identity ? { 'X-AgentCrew-Identity': identity } : {}) } }
}

export class ApiFailure extends Error {
  constructor(message: string, public readonly code: string, public readonly status: number) { super(message) }
}

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal, method?: string): Promise<T> {
  const generation = identityGeneration
  const { url, headers } = await connection()
  const response = await fetch(url + '/api' + path, {
    method: method ?? (body === undefined ? 'GET' : 'POST'),
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000)
  })
  const text = await response.text()
  const envelope = text ? JSON.parse(text) : {}
  if (generation !== identityGeneration) throw new ApiFailure('当前请求身份已更换', 'IDENTITY_CHANGED', 0)
  if (!response.ok) throw new ApiFailure(envelope.error?.message ?? `请求失败（${response.status}）`, envelope.error?.code ?? 'CONNECTION_ERROR', response.status)
  return envelope.data as T
}

export function useMemoryEvents(workspace: string, agent: string, enabled: boolean): { events: Frame[]; status: string; error: string } {
  const [events, setEvents] = useState<Frame[]>([])
  const [status, setStatus] = useState('正在连接')
  const [error, setError] = useState('')
  useEffect(() => {
    setEvents([]); setError('')
    if (!enabled) return
    const lifetime = new AbortController()
    let stream: AbortController | undefined
    let timer: ReturnType<typeof setTimeout> | undefined
    let cursor = 0
    const run = async (): Promise<void> => {
      let reset = false
      try {
        const { url, headers } = await connection()
        if (lifetime.signal.aborted) return
        const subscription = new AbortController()
        stream = subscription
        await fetchEventSource(`${url}/api/memory/stream?${new URLSearchParams({ workspace_id: workspace, agent_id: agent, from: String(cursor) })}`, {
          headers, signal: subscription.signal, openWhenHidden: true,
          async onopen(response) {
            if (!response.ok || !response.headers.get('content-type')?.startsWith('text/event-stream')) throw new Error(`记忆事件订阅失败（${response.status}）`)
            if (!lifetime.signal.aborted) { setStatus('已连接'); setError('') }
          },
          onmessage(message) {
            if (lifetime.signal.aborted || !message.data || message.event === 'ping') return
            if (message.event === 'resync') { reset = true; subscription.abort(); return }
            if (message.event === 'shutdown') { subscription.abort(); return }
            const frame = parseEventFrame(message.data)
            if (frame === null) return
            if (!Number.isSafeInteger(frame.global_seq) || typeof frame.type !== 'string') throw new Error('记忆事件信封无效')
            if (frame.global_seq <= cursor) return
            cursor = frame.global_seq
            setEvents((previous) => [...previous, frame])
          },
          onerror(reason) { throw reason },
          onclose() { throw new Error('记忆事件连接已关闭') }
        })
      } catch (reason) {
        if (!lifetime.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason))
      }
      if (lifetime.signal.aborted) return
      if (reset) { cursor = 0; setEvents([]) }
      setStatus('正在重新连接')
      timer = setTimeout(() => { void run() }, reset ? 0 : 1000)
    }
    void run()
    return () => { lifetime.abort(); stream?.abort(); clearTimeout(timer) }
  }, [workspace, agent, enabled])
  return { events, status, error }
}

export function useSession(id: string | null, revision = 0): {
  snapshot: Snapshot | null; events: Frame[]; scope: Scope | null; status: string; error: string; replayedThrough: number
} {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [events, setEvents] = useState<Frame[]>([])
  const [replayedThrough, setReplayedThrough] = useState(0)
  const [scope, setScope] = useState<Scope | null>(null)
  const [status, setStatus] = useState('正在连接')
  const [error, setError] = useState('')
  useEffect(() => {
    setSnapshot(null); setEvents([]); setReplayedThrough(0); setScope(null); setError('')
    if (!id) return
    const lifetime = new AbortController()
    let stream: AbortController | null = null
    let timer: ReturnType<typeof setTimeout> | undefined
    let cursor = 0
    let failures = 0
    let needsSnapshot = true
    let resyncRequested = false
    let refreshing = false
    let dirty = false
    const refresh = async (): Promise<void> => {
      dirty = true
      if (refreshing) return
      refreshing = true
      try {
        while (dirty && !lifetime.signal.aborted) {
          dirty = false
          const next = await api<Snapshot>(`/conversations/${id}/state`, undefined, lifetime.signal)
          if (!lifetime.signal.aborted) setSnapshot((previous) => !previous || next.at_global_seq >= previous.at_global_seq ? next : previous)
        }
      } finally { refreshing = false }
    }
    const read = async (from: number, receive: (event: Frame, subscription: AbortController) => void): Promise<void> => {
      const { url, headers } = await connection()
      if (lifetime.signal.aborted) return
      const subscription = new AbortController()
      stream = subscription
      await fetchEventSource(`${url}/api/conversations/${id}/stream?from=${from}`, {
        headers, signal: subscription.signal, openWhenHidden: true,
        async onopen(response) {
          if (lifetime.signal.aborted || subscription.signal.aborted) return
          if (!response.ok) throw new ApiFailure(`事件订阅权限或状态拒绝（${response.status}）`, 'STREAM_DENIED', response.status)
          if (!response.headers.get('content-type')?.startsWith('text/event-stream')) throw new Error(`事件订阅失败（${response.status}）`)
          if (!needsSnapshot) setStatus('已连接')
        },
        onmessage(message) {
          if (lifetime.signal.aborted || subscription.signal.aborted) return
          if (!message.data || message.event === 'ping') return
          if (message.event === 'resync') { resyncRequested = true; subscription.abort(); return }
          if (message.event === 'shutdown') {
            cursor = Math.max(cursor, Number(JSON.parse(message.data).last_continuous_global_seq))
            sessionStorage.setItem(`cursor:${id}`, String(cursor))
            subscription.abort(); return
          }
          const event = parseEventFrame(message.data)
          if (event === null) return
          if (!Number.isSafeInteger(event.global_seq) || typeof event.type !== 'string') throw new Error('事件信封无效')
          receive(event, subscription)
        },
        onerror(error) { throw error },
        onclose() { throw new Error('事件连接已关闭') }
      })
    }
    const run = async (): Promise<void> => {
      do {
        resyncRequested = false
        needsSnapshot = true
        setError('')
        try {
          setStatus('正在恢复')
          const next = await api<Snapshot>(`/conversations/${id}/state`, undefined, lifetime.signal)
          const nextScope = await api<Scope>(`/conversations/${id}/scope`, undefined, lifetime.signal)
          const history: Frame[] = []
          let complete = next.at_global_seq === 0
          if (!complete) await read(0, (event, subscription) => {
            if (event.global_seq <= next.at_global_seq) history.push(event)
            if (event.global_seq >= next.at_global_seq) { complete = true; subscription.abort() }
          })
          if (lifetime.signal.aborted) return
          if (!complete) throw new Error('历史恢复中断，正在重新连接')
          cursor = next.at_global_seq
          setEvents(history); setReplayedThrough(cursor); setSnapshot(next); setScope(nextScope)
          needsSnapshot = false
          if (lifetime.signal.aborted) return
          await read(cursor, (event, subscription) => {
            if (event.global_seq <= cursor) return
            cursor = event.global_seq
            failures = 0
            sessionStorage.setItem(`cursor:${id}`, String(cursor))
            setEvents((previous) => [...previous, event])
            void refresh().catch((reason: Error) => {
              if (!lifetime.signal.aborted && !subscription.signal.aborted) { setError(reason.message); subscription.abort() }
            })
          })
        } catch (reason) {
          if (reason instanceof ApiFailure && [401, 403].includes(reason.status)) { setSnapshot(null); setEvents([]); setScope(null); setReplayedThrough(0) }
          if (!lifetime.signal.aborted && !resyncRequested) setError(reason instanceof Error ? reason.message : String(reason))
        }
      } while (resyncRequested && !lifetime.signal.aborted)
      if (lifetime.signal.aborted) return
      setStatus('正在重新连接')
      timer = setTimeout(() => { setError(''); void run() }, Math.min(30000, 1000 * 2 ** Math.min(failures++, 5)))
    }
    void run()
    return () => { lifetime.abort(); stream?.abort(); clearTimeout(timer) }
  }, [id, revision])
  return { snapshot, events, scope, status, error, replayedThrough }
}
