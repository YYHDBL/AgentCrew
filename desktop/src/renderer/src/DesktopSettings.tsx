import { useLayoutEffect, useRef, useState } from 'react'
import { Button, Switch } from 'antd'
import { api, ApiFailure } from './session'
import type { components } from './api/generated/http'
import type { Identity } from './api/types'
import type { DesktopInfo } from '../../preload/types'

type Preferences = components['schemas']['DesktopPreferences']

const isRequestFailure = (reason: unknown): reason is Error => reason instanceof ApiFailure
  || (reason instanceof DOMException && ['AbortError', 'TimeoutError'].includes(reason.name))
  || (reason instanceof TypeError && reason.message === 'Failed to fetch')

export function DesktopSettings({ identity, enabled }: { identity: Identity | null; enabled: boolean }): JSX.Element {
  const [preferences, setPreferences] = useState<Preferences | null>(null)
  const [info, setInfo] = useState<DesktopInfo | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const lifetime = useRef<AbortController | null>(null)
  const canEdit = enabled && (identity?.role === 'owner' || identity?.role === 'admin')

  useLayoutEffect(() => {
    setPreferences(null); setInfo(null); setSaving(false); setMessage(''); setError('')
    if (!enabled) return
    const controller = new AbortController()
    lifetime.current = controller
    void Promise.all([api<Preferences>('/settings/desktop', undefined, controller.signal), window.agentcrew.getDesktopInfo()]).then(([value, desktop]) => {
      if (controller.signal.aborted) return
      setPreferences(value); setInfo(desktop)
    }, (reason: unknown) => {
      if (controller.signal.aborted) return
      if (!isRequestFailure(reason) && !(reason instanceof Error && reason.message.startsWith("Error invoking remote method 'desktop-info':"))) throw reason
      setError(reason.message)
    })
    return () => { controller.abort(); lifetime.current = null }
  }, [enabled, identity?.effective_user_id, identity?.role])

  const save = async (): Promise<void> => {
    const controller = lifetime.current
    if (!canEdit || !preferences || saving || !controller || controller.signal.aborted) return
    setSaving(true); setMessage(''); setError('')
    try {
      const value = await api<Preferences>('/settings/desktop', preferences, controller.signal, 'PATCH')
      if (controller.signal.aborted) return
      setPreferences(value)
      await window.agentcrew.desktopPreferencesChanged()
      if (controller.signal.aborted) return
      setMessage('桌面设置已保存。')
    } catch (reason) {
      if (controller.signal.aborted) return
      if (!isRequestFailure(reason) && !(reason instanceof Error && reason.message.startsWith("Error invoking remote method 'desktop-preferences-changed':"))) throw reason
      setError(reason.message)
    } finally {
      if (!controller.signal.aborted) setSaving(false)
    }
  }

  return <main className="memory-page desktop-settings" aria-busy={enabled && !preferences && !error}>
    <div className="memory-heading"><div><h1>桌面设置</h1><p>管理窗口关闭行为与运行期间的系统休眠设置。</p></div></div>
    {!enabled && <p role="status">等待任务服务连接与身份认证后读取设置。</p>}
    {enabled && !preferences && !error && <p role="status">正在读取桌面设置。</p>}
    {error && <p className="automation-error" role="alert">{error}</p>}
    {message && <p className="automation-note" role="status">{message}</p>}
    {preferences && <section aria-label="桌面行为">
      <div className="desktop-setting"><div><label htmlFor="close-to-quit">关闭窗口时退出应用</label><p id="close-to-quit-help">开启后，关闭窗口会请求退出确认。关闭后，应用驻留托盘，后台任务与计划继续运行。</p></div><Switch id="close-to-quit" aria-describedby="close-to-quit-help" checked={preferences.close_to_quit} disabled={!canEdit || saving} onChange={(checked) => { setPreferences({ ...preferences, close_to_quit: checked }); setMessage('') }} /></div>
      <div className="desktop-setting"><div><label htmlFor="prevent-sleep">运行期间防止系统自动休眠</label><p id="prevent-sleep-help">应用运行期间请求系统保持任务执行。系统强制休眠、电脑关机与应用退出仍会中断执行。</p></div><Switch id="prevent-sleep" aria-describedby="prevent-sleep-help" checked={preferences.prevent_sleep} disabled={!canEdit || saving} onChange={(checked) => { setPreferences({ ...preferences, prevent_sleep: checked }); setMessage('') }} /></div>
      {!canEdit && <p>只有所有者和管理员可以修改桌面设置。</p>}
      {canEdit && saving && <p role="status">正在保存桌面设置…</p>}
      <Button type="primary" disabled={!canEdit || saving} onClick={() => void save()}>保存桌面设置</Button>
    </section>}
    <section><h2>数据目录</h2>{info ? <><p className="desktop-path">{info.dataDir}</p><Button onClick={() => void window.agentcrew.openPath(info.dataDir)}>在文件管理器中定位数据目录</Button><p>电源事件日志：<span className="desktop-path">{info.powerLog}</span></p></> : <p>数据目录尚未读取。</p>}</section>
    <section className="memory-notice"><h2>任务与计划的运行边界</h2><p>应用真正退出或电脑关机期间，后台任务和定时计划无法执行。期间错过的计划会登记为错过，重新启动后不会补跑。系统休眠期间也无法保证计划按时执行，恢复后会重新核查到期状态。</p></section>
  </main>
}
