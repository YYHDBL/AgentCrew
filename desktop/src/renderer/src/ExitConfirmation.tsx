import { useEffect, useRef, useState } from 'react'
import { Button, Modal } from 'antd'
import type { ExitRequest } from '../../preload/types'

const sourceNames = { menu: '应用菜单', tray: '系统托盘', close: '关闭窗口', system: '系统退出' }

export function ExitConfirmation({ onPendingChange }: { onPendingChange(pending: boolean): void }): JSX.Element {
  const [request, setRequest] = useState<ExitRequest | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const cancelButton = useRef<HTMLButtonElement>(null)
  const previousFocus = useRef<HTMLElement | null>(null)

  useEffect(() => window.agentcrew.onExitConfirm((value) => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setRequest(value); setBusy(false); setError('')
    onPendingChange(true)
  }), [onPendingChange])

  useEffect(() => {
    if (!request) return
    const timer = setTimeout(() => { setRequest(null); onPendingChange(false) }, 60000)
    return () => clearTimeout(timer)
  }, [request, onPendingChange])

  const decide = async (confirm: boolean): Promise<void> => {
    if (busy) return
    setBusy(true); setError('')
    let finished = false
    try {
      if (confirm) await window.agentcrew.confirmExit()
      else await window.agentcrew.cancelExit()
      finished = true
      setRequest(null)
      if (!confirm) onPendingChange(false)
    } finally {
      setBusy(false)
      if (!finished) setError('退出决定未能提交，请重新操作。')
    }
  }

  const impact = request?.impact
  return <Modal title="退出 AgentCrew" open={request !== null} width={680} maskClosable={false} closable={!busy} keyboard={!busy} onCancel={() => void decide(false)}
    afterOpenChange={(open) => { if (open) cancelButton.current?.focus(); else previousFocus.current?.focus() }}
    footer={<><Button ref={cancelButton} disabled={busy} onClick={() => void decide(false)}>取消退出</Button><Button danger type="primary" disabled={busy} loading={busy} onClick={() => void decide(true)}>确认退出</Button></>}>
    <div className="exit-impact" aria-busy={busy}>
      <p>退出来源：{request ? sourceNames[request.source] : ''}。退出后，正在执行的任务、等待审批与后台作业将停止。</p>
      <p className="memory-notice">应用退出或电脑关机期间，定时计划无法执行。错过的计划会登记为错过，重新启动后不会补跑。</p>
      {error && <p className="automation-error" role="alert">{error}</p>}
      {request?.impactError && <p className="automation-error" role="alert">退出影响读取失败：{request.impactError}。当前无法确认受影响的任务数量。</p>}
      {!impact && !request?.impactError && <p role="alert">退出影响快照不可用，当前无法确认受影响的任务数量。</p>}
      {impact && <>
        <p>快照时间：{new Date(impact.observed_at).toLocaleString('zh-CN')} · 事件水位：{impact.at_global_seq}</p>
        <section><h3>活跃任务（{impact.active_runs.length}）</h3>{impact.active_runs.length ? <ul>{impact.active_runs.map((run) => <li key={run.id}>{run.instruction} · 运行记录：{run.id}</li>)}</ul> : <p>当前没有活跃任务。</p>}</section>
        <section><h3>待核验操作（{impact.pending_verifications.length}）</h3>{impact.pending_verifications.length ? <ul>{impact.pending_verifications.map((verification, index) => <li key={verification.call_id ?? index}>{verification.tool} · 调用编号：{verification.call_id}{verification.evidence && <p>{verification.evidence}</p>}</li>)}</ul> : <p>当前没有待核验操作。</p>}</section>
        <section><h3>后台作业（{impact.background_jobs.length}）</h3>{impact.background_jobs.length ? <p>退出将停止 {impact.background_jobs.length} 项后台作业。</p> : <p>当前没有后台作业。</p>}</section>
        <section><h3>未来 24 小时计划（{impact.upcoming_jobs.length}）</h3>{impact.upcoming_jobs.length ? <ul>{impact.upcoming_jobs.map((job) => <li key={job.id}>{job.name} · 下一次运行：{job.state.next_run_at === null ? '暂无时间' : new Date(job.state.next_run_at).toLocaleString('zh-CN', { timeZone: job.schedule.tz })}</li>)}</ul> : <p>未来 24 小时没有到期计划。</p>}</section>
      </>}
    </div>
  </Modal>
}
