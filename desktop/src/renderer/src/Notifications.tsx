import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Button, Pagination, Tag } from 'antd'
import { api, ApiFailure } from './session'
import type { Notification } from './api/types'
import type { operations } from './api/generated/http'

type NotificationsPage = operations['listNotifications']['responses'][200]['content']['application/json']['data']
type Acknowledgement = operations['acknowledgeNotification']['responses'][200]['content']['application/json']['data']
const severityNames = { badge: '消息', warning: '警告', error: '错误' }
const isRequestFailure = (reason: unknown): reason is Error => reason instanceof ApiFailure
  || (reason instanceof DOMException && ['AbortError', 'TimeoutError'].includes(reason.name))
  || (reason instanceof TypeError && reason.message === 'Failed to fetch')

export function useNotifications(enabled: boolean, scope: string, paused = false): {
  items: Notification[]; unread: number; loading: boolean; error: string; deliveryNotice: string
  refresh(): void; read(id: string): Promise<Notification | null>
} {
  const [items, setItems] = useState<Notification[]>([])
  const [unread, setUnread] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [deliveryNotice, setDeliveryNotice] = useState('')
  const [revision, setRevision] = useState(0)
  const lifetime = useRef<AbortController | null>(null)
  const refresh = (): void => setRevision((value) => value + 1)

  useLayoutEffect(() => {
    lifetime.current?.abort()
    setItems([]); setUnread(0); setError(''); setDeliveryNotice('')
    setLoading(false)
  }, [enabled, scope])

  useEffect(() => {
    if (!enabled || paused) return
    const controller = new AbortController()
    lifetime.current = controller
    let timer: ReturnType<typeof setTimeout> | undefined
    const consume = async (): Promise<void> => {
      setLoading(true)
      const records: Notification[] = []
      const cursors = new Set<string>()
      let after: string | null = null
      let count = 0
      do {
        const query = new URLSearchParams({ limit: '200' })
        if (after) query.set('after', after)
        const page = await api<NotificationsPage>(`/notifications?${query}`, undefined, controller.signal)
        if (controller.signal.aborted) return
        if (!after) count = page.unread_count
        records.push(...page.items)
        after = page.next_after
        if (after && cursors.has(after)) throw new Error('通知分页游标重复，停止读取')
        if (after) cursors.add(after)
      } while (after)
      setItems(records); setUnread(count)
      for (const notification of records) {
        if (controller.signal.aborted) return
        if (notification.severity === 'badge' || notification.presented_at !== null || notification.read_at !== null) continue
        const result = await api<Acknowledgement>(`/notifications/${encodeURIComponent(notification.id)}`, { action: 'presented' }, controller.signal, 'PATCH')
        if (controller.signal.aborted) return
        setItems((values) => values.map((item) => item.id === result.notification.id ? result.notification : item))
        if (!result.claimed) continue
        const detail = result.notification
        const delivered = await window.agentcrew.presentNotification({
          title: `AgentCrew · ${severityNames[detail.severity]}`,
          body: detail.message.slice(0, 1000),
          notification: { id: detail.id, job_id: detail.job_id, task_run_id: detail.task_run_id }
        })
        if (controller.signal.aborted) return
        if (!delivered.delivered) setDeliveryNotice(delivered.reason ?? '系统提醒未送达，请在通知中心查看。')
      }
      setLoading(false); setError('')
    }
    const run = async (): Promise<void> => {
      await consume().then(undefined, (reason: unknown) => {
        if (controller.signal.aborted) return
        if (!isRequestFailure(reason) && !(reason instanceof Error && reason.message.startsWith("Error invoking remote method 'present-notification':"))) throw reason
        setItems([]); setUnread(0); setLoading(false); setError(reason.message)
      })
      if (controller.signal.aborted) return
      timer = setTimeout(() => void run(), 5000)
    }
    void run()
    return () => { controller.abort(); clearTimeout(timer); lifetime.current = null }
  }, [enabled, scope, revision, paused])

  const read = async (id: string): Promise<Notification | null> => {
    const controller = lifetime.current
    if (!enabled || !controller || controller.signal.aborted) return null
    return api<Acknowledgement>(`/notifications/${encodeURIComponent(id)}`, { action: 'read' }, controller.signal, 'PATCH').then((result) => {
      if (controller.signal.aborted) return null
      refresh()
      return result.notification
    }, (reason: unknown) => {
      if (controller.signal.aborted) return null
      if (!isRequestFailure(reason)) throw reason
      setError(reason.message)
      return null
    })
  }

  return { items: enabled ? items : [], unread: enabled ? unread : 0, loading, error, deliveryNotice, refresh, read }
}

export function NotificationCenter({ notifications, enabled, onRun, onJob }: {
  notifications: ReturnType<typeof useNotifications>; enabled: boolean
  onRun(id: string): void; onJob(notification: Notification): void
}): JSX.Element {
  const [page, setPage] = useState(1)
  const [reading, setReading] = useState<string | null>(null)
  const current = Math.min(page, Math.max(1, Math.ceil(notifications.items.length / 20)))
  const read = async (id: string): Promise<void> => {
    setReading(id)
    try { await notifications.read(id) }
    finally { setReading(null) }
  }
  return <main className="memory-page notification-center" aria-busy={notifications.loading}>
    <div className="memory-heading"><div><h1>通知中心</h1><p>未读通知 {notifications.unread} 条。消息显示在应用内，警告和错误会请求系统提醒。</p></div><Button disabled={!enabled || notifications.loading} onClick={notifications.refresh}>刷新通知</Button></div>
    {!enabled && <p role="status">等待任务服务连接与身份认证后读取通知。</p>}
    {notifications.error && <p className="automation-error" role="alert">{notifications.error}</p>}
    {notifications.deliveryNotice && <p className="memory-notice" role="status">{notifications.deliveryNotice}</p>}
    {enabled && notifications.loading && notifications.items.length === 0 && <p role="status">正在读取通知。</p>}
    {enabled && !notifications.loading && !notifications.error && notifications.items.length === 0 && <p>当前没有通知。</p>}
    <ol className="notification-list">{notifications.items.slice((current - 1) * 20, current * 20).map((notification) => <li key={notification.id} className={notification.read_at ? 'notification-read' : 'notification-unread'}>
      <div className="notification-heading"><Tag color={notification.severity === 'error' ? 'red' : notification.severity === 'warning' ? 'orange' : 'blue'}>{severityNames[notification.severity]}</Tag><span>{notification.read_at ? '已读' : '未读'}</span><time dateTime={notification.created_at}>{new Date(notification.created_at).toLocaleString('zh-CN')}</time></div>
      <p>{notification.message}</p>
      <div className="card-actions"><Button disabled={Boolean(notification.read_at) || reading !== null || !enabled} loading={reading === notification.id} onClick={() => void read(notification.id)}>标记已读</Button>{notification.task_run_id && <Button disabled={!enabled} onClick={() => onRun(notification.task_run_id!)}>查看运行记录</Button>}{notification.job_id && <Button disabled={!enabled} onClick={() => onJob(notification)}>查看关联计划</Button>}</div>
    </li>)}</ol>
    {notifications.items.length > 20 && <Pagination aria-label="通知分页" current={current} pageSize={20} total={notifications.items.length} showSizeChanger={false} onChange={setPage} />}
  </main>
}
