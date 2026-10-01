import { useEffect, useState } from 'react'
import { Button } from 'antd'
import type { Frame } from './session'

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

export function Details({ events, action, busy }: { events: Frame[]; action: Action; busy: boolean }): JSX.Element {
  const resolved = new Set(events.filter((e) => e.type === 'permission.resolved').map((e) => e.payload.tool_call_id))
  const answered = new Set(events.filter((e) => e.type === 'question.answered').map((e) => e.payload.request_id))
  const terminal = new Set(events.filter((e) => ['run.completed', 'run.failed', 'run.cancelled', 'run.interrupted'].includes(e.type)).map((e) => e.task_run_id))
  const completedSteps = new Set(events.filter((e) => e.type === 'step.completed').map((e) => e.payload.step_id))
  const latestRequests = new Map<unknown, Frame>()
  for (const event of events) {
    if (event.type.startsWith('llm.request_')) latestRequests.set(event.payload.step_id, event)
  }
  const tools = new Map<unknown, Frame[]>()
  for (const event of events) {
    if (!event.type.startsWith('tool.')) continue
    const calls = tools.get(event.payload.call_id) ?? []
    calls.push(event)
    tools.set(event.payload.call_id, calls)
  }
  return <div className="details-scroll">{events.map((event) => {
    const p = event.payload
    if (event.type === 'permission.requested' && !resolved.has(p.tool_call_id) && !terminal.has(event.task_run_id)) return <section className="process-card approval" key={event.global_seq}>
      <h3>等待审批：{String(p.tool)}</h3><p>风险等级：{String(p.risk)}</p><p>目标：<span className="file-path">{String(p.target)}</span></p><p>持续授权范围：<span className="file-path">{String(p.always_scope_preview)}</span></p>
      <div className="card-actions">{decisions.map(([decision, label]) => <Button key={decision} disabled={busy} onClick={() => action(`/tool-approvals/${p.tool_call_id}`, { decision, input_hash: p.input_hash })}>{label}</Button>)}</div>
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
    return null
  })}</div>
}
