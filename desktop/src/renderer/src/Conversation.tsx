import { useEffect, useState } from 'react'
import { Button, Modal } from 'antd'
import { api, type Frame } from './session'
import { MemoryNotice } from './Memory'

export function TypedText({ text }: { text: string }): JSX.Element {
  const [length, setLength] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches ? text.length : 0)
  useEffect(() => {
    if (length >= text.length) return
    const timer = setInterval(() => setLength((value) => Math.min(text.length, value + 2)), 18)
    return () => clearInterval(timer)
  }, [text, length])
  return <p className="message-text">{text.slice(0, length)}</p>
}

export function Chat({ events, replayedThrough }: { events: Frame[]; replayedThrough: number }): JSX.Element {
  return <div className="messages" aria-label="聊天记录">{events.map((event) => {
    const p = event.payload
    const user = event.type === 'queue.item_enqueued' || (event.type === 'run.queued' && !p.queue_item_id)
    const assistant = event.type === 'llm.request_done' && Array.isArray(p.tool_uses) && p.tool_uses.length === 0 && Boolean(p.text)
    if (['memory.updated', 'memory.archived', 'skill.patched', 'memory.curated', 'context.compacted'].includes(event.type)) return <MemoryNotice key={event.global_seq} events={[event]} />
    if (!user && !assistant) return null
    return <article className={`message ${user ? 'user' : 'assistant'}`} key={event.global_seq}>
      <strong>{user ? '你' : '数字员工'}</strong>
      {user || event.global_seq <= replayedThrough ? <p className="message-text">{String(p.text ?? p.instruction)}</p> : <TypedText text={String(p.text)} />}
    </article>
  })}</div>
}

function Question({ event, action, busy }: { event: Frame; action: Action; busy: boolean }): JSX.Element {
  const [answer, setAnswer] = useState('')
  const p = event.payload
  return <section className="process-card"><h3>需要回答</h3><p>{String(p.question)}</p>
    {Array.isArray(p.options) && <p>{p.options.map(String).join(' / ')}</p>}
    <textarea aria-label="问题回答" value={answer} onChange={(e) => setAnswer(e.target.value)} />
    <div className="card-actions"><Button disabled={busy || !answer.trim()} onClick={() => action(`/questions/${p.request_id}/answer`, { answer })}>提交回答</Button>
      <Button disabled={busy} onClick={() => action(`/questions/${p.request_id}/answer`, { answer: null })}>取消回答</Button></div>
  </section>
}

type Action = (path: string, body: unknown) => void
const decisions = [['allow_once', '允许'], ['allow_always', '总是允许'], ['reject_once', '拒绝'], ['reject_always', '总是拒绝']]

export function Details({ events, action, busy, canManage = true }: { events: Frame[]; action: Action; busy: boolean; canManage?: boolean }): JSX.Element {
  const [permanent, setPermanent] = useState<{ call: Record<string, unknown>; decision: string } | null>(null)
  const resolved = new Set(events.filter((e) => e.type === 'permission.resolved').map((e) => e.payload.tool_call_id))
  const answered = new Set(events.filter((e) => e.type === 'question.answered').map((e) => e.payload.request_id))
  const completedSteps = new Set(events.filter((e) => e.type === 'step.completed').map((e) => e.payload.step_id))
  const lifecycles = new Map<string, Frame>()
  const interruptedThrough = new Map<string, number>()
  const latestRequests = new Map<unknown, Frame>()
  const jobStates = new Map<unknown, Frame>()
  const jobApprovals = new Map<unknown, Frame>()
  for (const event of events) {
    if (event.task_run_id && event.type === 'run.interrupted') interruptedThrough.set(event.task_run_id, Math.max(interruptedThrough.get(event.task_run_id) ?? 0, event.global_seq))
    if (event.task_run_id && ['run.queued', 'run.started', 'run.resumed', 'run.completed', 'run.failed', 'run.cancelled', 'run.interrupted'].includes(event.type)) {
      const latest = lifecycles.get(event.task_run_id)
      if (!latest || event.global_seq > latest.global_seq) lifecycles.set(event.task_run_id, event)
    }
    if (event.type.startsWith('llm.request_')) latestRequests.set(event.payload.step_id, event)
    if (event.type === 'memory.job_status') jobStates.set(event.payload.job_id, event)
    if (event.type === 'memory.approval_resolved') jobApprovals.set(event.payload.approval_id, event)
  }
  const terminal = new Set([...lifecycles.values()].filter((e) => ['run.completed', 'run.failed', 'run.cancelled', 'run.interrupted'].includes(e.type)).map((e) => e.task_run_id))
  const tools = new Map<unknown, Frame[]>()
  for (const event of events) {
    if (!event.type.startsWith('tool.')) continue
    const calls = tools.get(event.payload.call_id) ?? []
    calls.push(event)
    tools.set(event.payload.call_id, calls)
  }
  return <div className="details-scroll">{events.map((event) => {
    const p = event.payload
    if (event.type === 'memory.approval_requested') {
      const state = jobStates.get(p.job_id)?.payload.status
      const decision = jobApprovals.get(p.approval_id)?.payload.decision
      const active = !decision && state === 'waiting_approval'
      return <section className="process-card memory-approval" key={event.global_seq}><h3>{active ? '后台等待审批' : decision === 'expired' || ['cancelled', 'interrupted', 'failed'].includes(String(state)) ? '后台审批已失效' : '后台审批记录'}：{String(p.tool)}</h3><p>作业：{String(p.job_id)}</p><pre>{JSON.stringify(p.input, null, 2)}</pre>{active ? <div className="card-actions">{[['allow_once', '本次允许'], ['reject_once', '本次拒绝']].map(([decision, label]) => <Button disabled={busy} key={decision} onClick={() => action(`/memory/jobs/${p.job_id}/approvals/${p.approval_id}`, { decision, input_hash: p.input_hash })}>{label}</Button>)}</div> : <p>{decision === 'allow_once' ? '已本次允许' : decision === 'reject_once' ? '已本次拒绝' : '此审批已经停止执行。'}</p>}</section>
    }
    if (event.type === 'memory.job_status' && jobStates.get(p.job_id)?.global_seq === event.global_seq) return <section className="process-card" key={event.global_seq}><h3>后台作业：{String(p.kind)}</h3><p>状态：{String(p.status)}</p>{p.error ? <p>{String(p.error)}</p> : null}<p>前台来源任务：{String(p.source_task_run_id ?? '独立治理')}</p>{['queued', 'running', 'waiting_approval'].includes(String(p.status)) && <Button disabled={busy} onClick={() => action(`/memory/jobs/${p.job_id}/cancel`, {})}>取消后台作业</Button>}</section>
    if (event.type === 'context.compacted') return <section className="process-card" key={event.global_seq}><h3>上下文已压缩</h3><p>{String(p.before_tokens)} → {String(p.after_tokens)} token</p><p>检查点：{String(p.checkpoint_id)}</p></section>
    if (event.type === 'permission.requested' && !resolved.has(p.tool_call_id) && event.global_seq < (interruptedThrough.get(event.task_run_id ?? '') ?? 0)) return <section className="process-card" key={event.global_seq}>
      <h3>审批已失效</h3><p>任务中断时此调用已停止，审批请求已失效。</p><p>工具：{String(p.tool)}</p><p>目标：<span className="file-path">{String(p.target)}</span></p>
    </section>
    if (event.type === 'permission.requested' && !resolved.has(p.tool_call_id) && !terminal.has(event.task_run_id)) return <section className="process-card approval" key={event.global_seq}>
      <h3>等待审批：{String(p.tool)}</h3><p>风险等级：{String(p.risk)}</p><p>目标：<span className="file-path">{String(p.target)}</span></p><p>持续授权范围：<span className="file-path">{String(p.always_scope_preview)}</span></p>
      <div className="card-actions">{decisions.map(([decision, label]) => <Button key={decision} disabled={busy || (decision.endsWith('always') && !canManage)} onClick={() => { if (decision.endsWith('always')) setPermanent({ call: p, decision }); else action(`/tool-approvals/${p.tool_call_id}`, { decision, input_hash: p.input_hash }) }}>{label}</Button>)}</div>{!canManage && <p>永久规则只能由owner或admin保存。</p>}
    </section>
    if (event.type === 'question.requested' && !answered.has(p.request_id) && !terminal.has(event.task_run_id)) return <Question key={event.global_seq} event={event} action={action} busy={busy} />
    if (event.type === 'step.started') {
      const done = completedSteps.has(p.step_id)
      return <section className="process-card" key={event.global_seq}><h3>步骤 {String(p.ordinal)}</h3><p>{done ? '已完成' : terminal.has(event.task_run_id) ? '已结束' : '正在执行'}</p></section>
    }
    if (event.type === 'tool.prepared') {
      const updates = tools.get(p.call_id)!
      const latest = updates[updates.length - 1]
      return <section className="process-card" key={event.global_seq}><h3>{String(p.tool_name)}</h3><p>{latest.type}</p>
        <details><summary>调用参数</summary><pre>{JSON.stringify(p.input, null, 2)}</pre></details>
        {updates.filter((e) => e.payload.output_summary || e.payload.error || e.payload.artifact_path).map((e) => <div key={e.global_seq}><pre>{String(e.payload.output_summary ?? e.payload.error ?? '')}</pre>{e.payload.artifact_path ? <p>工件：{String(e.payload.artifact_path)}</p> : null}</div>)}
      </section>
    }
    if (event.type === 'materials.imported') return <section className="process-card" key={event.global_seq}><h3>任务材料</h3>{Array.isArray(p.files) && p.files.map((file: Record<string, unknown>) => <p key={String(file.original_path)}><span className="file-path">{String(file.original_path)}</span>{file.error ? `导入失败：${String(file.error)}` : `已导入：${String(file.stored_name)}（${String(file.size_bytes)} 字节）`}</p>)}{Array.isArray(p.folders) && p.folders.map((folder: Record<string, unknown>) => <p key={String(folder.path)}><span className="file-path">{String(folder.path)}</span>{folder.error ? `授权失败：${String(folder.error)}` : '文件夹已授权'}</p>)}</section>
    if (event.type === 'llm.request_failed') {
      const latest = latestRequests.get(p.step_id)!
      const outcome = latest.type === 'llm.request_done' ? '已恢复' : latest.type === 'llm.request_started' && !terminal.has(event.task_run_id) ? '正在重试' : terminal.has(event.task_run_id) ? '已结束' : '等待重试'
      return <section className="process-card" key={event.global_seq}><h3>模型请求失败 · {outcome}</h3><p>{String(p.error)}</p></section>
    }
    if (event.type === 'run.failed') return <section className="process-card" role="alert" key={event.global_seq}><h3>执行错误</h3><p>{String(p.reason)}</p></section>
    if (event.type === 'run.interrupted') return <section className="process-card" key={event.global_seq}><h3>任务已中断</h3><p>{String(p.reason)}</p></section>
    if (event.type === 'run.resumed') return <section className="process-card" key={event.global_seq}><h3>已恢复 · 第 {String(p.attempt_no)} 次尝试</h3></section>
    return null
  })}<Modal title="确认永久权限规则" open={Boolean(permanent)} okText="确认保存永久规则" cancelText="取消决定" confirmLoading={busy} onCancel={() => setPermanent(null)} onOk={() => { if (permanent) { action(`/tool-approvals/${permanent.call.tool_call_id}`, { decision: permanent.decision, input_hash: permanent.call.input_hash }); setPermanent(null) } }}><p>工具：{String(permanent?.call.tool)}</p><p>持续授权范围：{String(permanent?.call.always_scope_preview)}</p><p>{permanent?.decision === 'allow_always' ? '在此范围保存允许规则；后续执行继续受当前scope、protected、角色与Grant限制。' : '在此范围保存deny规则，后续匹配调用直接拒绝。'}</p></Modal></div>
}

export interface PendingVerification { call_id: string; tool: string; input: Record<string, unknown>; dispatched_at: string; evidence: string }

export function VerificationCards({ calls, refreshed }: { calls: PendingVerification[]; refreshed: () => void }): JSX.Element | null {
  const [note, setNote] = useState('')
  const [choice, setChoice] = useState<{ call: PendingVerification; verdict: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (!calls.length) return null
  const submit = async (): Promise<void> => {
    if (!choice) return
    setBusy(true); setError('')
    try { await api(`/tool-calls/${choice.call.call_id}/verification`, { verdict: choice.verdict, note }); setChoice(null); setNote(''); refreshed() }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }
  return <section className="verification-calls" aria-label="待核验副作用"><h2>等待真实效果核验</h2><p>核验结束前，后续任务、模型请求与工具派发均停止。</p>{calls.map((call) => <article className="process-card" key={call.call_id}><h3>{call.tool} · 待核验</h3><p>调用：{call.call_id} · 派发时间：{new Date(call.dispatched_at).toLocaleString('zh-CN')}</p><pre>{JSON.stringify(call.input, null, 2)}</pre><p>{call.evidence}</p><div className="card-actions"><Button disabled={busy} onClick={() => setChoice({ call, verdict: 'confirmed_executed' })}>核验为已执行</Button><Button disabled={busy} onClick={() => setChoice({ call, verdict: 'confirmed_not_executed' })}>核验为未执行</Button></div></article>)}<Modal title="提交真实效果核验" open={Boolean(choice)} okText="确认核验结果" cancelText="取消核验" confirmLoading={busy} onCancel={() => { if (!busy) setChoice(null) }} onOk={() => void submit()}><p>调用：{choice?.call.call_id} · 结果：{choice?.verdict === 'confirmed_executed' ? '已执行' : '未执行'}</p><p>请根据实际文件、上游记录或操作次数核查效果。核验继续使用当前身份与权限。</p><label className="governance-field"><span>核验依据</span><textarea value={note} onChange={(event) => setNote(event.target.value)} /></label>{error && <p role="alert">{error}</p>}</Modal></section>
}
