import { useEffect, useState } from 'react'
import { fetchEventSource } from '@microsoft/fetch-event-source'

export interface Conversation { id: string; title: string | null; state_badge: string; last_activity_at: string; agent_name: string }
export interface Snapshot {
  state: string; at_global_seq: number; waiting_approvals: number; waiting_questions: number
  queue_paused: boolean; queue: { id: string; text: string }[]
  can_send: boolean; can_queue: boolean; can_cancel: boolean; can_continue_queue: boolean
  current_task_run_id: string | null
}
export interface Frame {
  global_seq: number; type: string; task_run_id?: string; ts: string
  payload: Record<string, unknown>
}
export interface Scope { workspace_dir: string; materials_dir: string; folders: { path: string; access: string }[] }

export async function connection(): Promise<{ url: string; headers: Record<string, string> }> {
  const [port, token] = await Promise.all([window.agentcrew.getBackendPort(), window.agentcrew.getToken()])
  if (!port || !token) throw new Error('任务服务尚未就绪')
  return { url: `http://127.0.0.1:${port}`, headers: { Authorization: `Bearer ${token}` } }
}

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const { url, headers } = await connection()
  const response = await fetch(url + '/api' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000)
  })
  const text = await response.text()
  const envelope = text ? JSON.parse(text) : {}
  if (!response.ok) throw new Error(envelope.error?.message ?? `请求失败（${response.status}）`)
  return envelope.data as T
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
          if (!response.ok || !response.headers.get('content-type')?.startsWith('text/event-stream')) throw new Error(`事件订阅失败（${response.status}）`)
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
          const event = JSON.parse(message.data) as Frame
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
