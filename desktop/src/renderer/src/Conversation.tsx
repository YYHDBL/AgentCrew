import { useState } from 'react'
import { Button, Modal, Tag, Tooltip } from 'antd'
import { CheckCircleOutlined, ClockCircleOutlined, FileTextOutlined, LoadingOutlined, TeamOutlined } from '@ant-design/icons'
import { api, type Frame } from './session'
import { MemoryNotice } from './Memory'
import { CopyButton, MessageContent } from './MessageContent'
import { displayTime, jobNames, statusNames, toolNames, toolStatusNames, toolTarget } from './conversation-view'
import type { components } from './api/generated/http'

export function Chat({ events, agentName }: { events: Frame[]; agentName: string }): JSX.Element {
  const modelSlots = new Map(events.filter((event) => event.type === 'llm.request_started').map((event) => [event.payload.llm_call_id, event.payload.model_slot]))
  return <div className="messages" aria-label="聊天记录">{events.map((event) => {
    const p = event.payload
    const user = event.type === 'queue.item_enqueued' || (event.type === 'run.queued' && !p.queue_item_id)
    const assistant = event.type === 'llm.request_done' && Boolean(p.text) && modelSlots.get(p.llm_call_id) !== 'aux'
    if (['memory.updated', 'memory.archived', 'skill.patched', 'memory.curated', 'context.compacted'].includes(event.type)) return <MemoryNotice key={event.global_seq} events={[event]} />
    if (!user && !assistant) return null
    return <article className={`message ${user ? 'user' : 'assistant'}`} key={event.global_seq}>
      {!user && <div className="message-author"><span className="assistant-avatar" aria-hidden="true"><TeamOutlined /></span><strong>{agentName}</strong><time dateTime={event.ts}>{displayTime(event.ts)}</time></div>}
      {user ? <p className="message-text">{String(p.text ?? p.instruction)}</p> : <MessageContent text={String(p.text)} />}
      <div className="message-actions">{user && <time dateTime={event.ts}>{displayTime(event.ts)}</time>}<CopyButton text={String(p.text ?? p.instruction)} label={user ? '复制指令' : '复制回复'} /></div>
    </article>
  })}</div>
}

export function ExecutionSummary({ events, onDetails }: { events: Frame[]; onDetails(): void }): JSX.Element | null {
  const prepared = events.filter((event) => event.type === 'tool.prepared')
  const completed = new Set(events.filter((event) => ['tool.completed', 'tool.skipped_idempotent'].includes(event.type)).map((event) => event.payload.call_id))
  const failed = new Set(events.filter((event) => event.type === 'tool.failed').map((event) => event.payload.call_id))
  if (!prepared.length) return null
  return <details className="execution-overview"><summary><ClockCircleOutlined /><span>执行记录</span><small>{prepared.length} 次调用 · {completed.size} 次已完成{failed.size ? ` · ${failed.size} 次失败` : ''}</small></summary><ol>{prepared.map((event) => {
    const p = event.payload
    const input = p.input as Record<string, unknown>
    return <li key={event.global_seq}><span className={`status-dot ${failed.has(p.call_id) ? 'status-failed' : completed.has(p.call_id) ? 'status-completed' : 'status-running'}`} /><div><strong>{toolNames[String(p.tool_name)] ?? String(p.tool_name)}</strong>{toolTarget(input) && <span className="file-path">{toolTarget(input)}</span>}</div><time dateTime={event.ts}>{displayTime(event.ts)}</time></li>
  })}</ol><Button type="text" size="small" onClick={onDetails}>查看调用参数与结果</Button></details>
}

export function Artifacts({ items }: { items: components['schemas']['Artifact'][] }): JSX.Element | null {
  if (!items.length) return null
  const names = { generating: '生成中', ready: '已生成', failed: '生成失败', missing: '文件缺失' }
  return <section className="artifact-list" aria-label="任务产物"><h2>任务产物</h2>{items.map((item) => <article className="artifact-card" key={item.id}>
    <span className="artifact-icon"><FileTextOutlined /></span><div><strong>{item.name}</strong><span className="file-path">{item.path}</span>{typeof item.size_bytes === 'number' && <small>{item.size_bytes < 1024 ? `${item.size_bytes} 字节` : `${(item.size_bytes / 1024).toFixed(1)} KB`}</small>}</div>
    <Tag color={item.status === 'ready' ? 'green' : item.status === 'failed' || item.status === 'missing' ? 'red' : 'gold'}>{item.status ? names[item.status] : '正在读取'}</Tag>
    <Tooltip title={item.status === 'ready' ? '在文件管理器中定位产物' : '文件可用后可以定位'}><Button size="small" disabled={item.status !== 'ready' || !item.path} onClick={() => void window.agentcrew.openPath(item.path!)}>定位文件</Button></Tooltip>
  </article>)}</section>
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
const decisions = [['allow_once', '仅本次允许'], ['allow_always', '始终允许'], ['reject_once', '仅本次拒绝'], ['reject_always', '始终拒绝']]
const riskNames: Record<string, string> = { low: '低风险', medium: '中等风险', high: '高风险' }

export function Details({ events, action, busy, canManage = true, mode = 'history' }: { events: Frame[]; action: Action; busy: boolean; canManage?: boolean; mode?: 'history' | 'attention' }): JSX.Element {
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
  return <div className={mode === 'attention' ? 'attention-cards' : 'details-scroll'}>{events.map((event) => {
    const p = event.payload
    const foregroundApproval = event.type === 'permission.requested' && !resolved.has(p.tool_call_id) && !terminal.has(event.task_run_id) && event.global_seq > (interruptedThrough.get(event.task_run_id ?? '') ?? 0)
    const foregroundQuestion = event.type === 'question.requested' && !answered.has(p.request_id) && !terminal.has(event.task_run_id)
    const backgroundApproval = event.type === 'memory.approval_requested' && !jobApprovals.has(p.approval_id) && jobStates.get(p.job_id)?.payload.status === 'waiting_approval'
    const needsAttention = foregroundApproval || foregroundQuestion || backgroundApproval
    if (mode === 'attention' ? !needsAttention : needsAttention) return null
    if (event.type === 'memory.approval_requested') {
      const state = jobStates.get(p.job_id)?.payload.status
      const decision = jobApprovals.get(p.approval_id)?.payload.decision
      const active = !decision && state === 'waiting_approval'
      return <section className="process-card memory-approval" key={event.global_seq}><h3>{active ? '后台等待审批' : decision === 'expired' || ['cancelled', 'interrupted', 'failed'].includes(String(state)) ? '后台审批已失效' : '后台审批记录'}：{String(p.tool)}</h3><p>作业：{String(p.job_id)}</p><pre>{JSON.stringify(p.input, null, 2)}</pre>{active ? <div className="card-actions">{[['allow_once', '本次允许'], ['reject_once', '本次拒绝']].map(([decision, label]) => <Button disabled={busy} key={decision} onClick={() => action(`/memory/jobs/${p.job_id}/approvals/${p.approval_id}`, { decision, input_hash: p.input_hash })}>{label}</Button>)}</div> : <p>{decision === 'allow_once' ? '已本次允许' : decision === 'reject_once' ? '已本次拒绝' : '此审批已经停止执行。'}</p>}</section>
    }
    if (event.type === 'memory.job_status' && jobStates.get(p.job_id)?.global_seq === event.global_seq) return <details className="background-record" key={event.global_seq}><summary>{jobNames[String(p.kind)] ?? String(p.kind)}<span>{statusNames[String(p.status)] ?? String(p.status)}</span></summary>{p.error ? <p role="alert">{String(p.error)}</p> : null}<p>来源执行编号：{String(p.source_task_run_id ?? '独立后台作业')}</p>{['queued', 'running', 'waiting_approval'].includes(String(p.status)) && <Button disabled={busy} onClick={() => action(`/memory/jobs/${p.job_id}/cancel`, {})}>取消后台作业</Button>}</details>
    if (event.type === 'context.compacted') return <section className="process-card" key={event.global_seq}><h3>上下文已压缩</h3><p>{String(p.before_tokens)} → {String(p.after_tokens)} token</p><p>检查点：{String(p.checkpoint_id)}</p></section>
    if (event.type === 'permission.requested' && !resolved.has(p.tool_call_id) && event.global_seq < (interruptedThrough.get(event.task_run_id ?? '') ?? 0)) return <section className="process-card" key={event.global_seq}>
      <h3>审批已失效</h3><p>任务中断时此调用已停止，审批请求已失效。</p><p>工具：{String(p.tool)}</p><p>目标：<span className="file-path">{String(p.target)}</span></p>
    </section>
    if (event.type === 'permission.requested' && !resolved.has(p.tool_call_id) && !terminal.has(event.task_run_id)) return <section className="process-card approval" key={event.global_seq}>
      <div className="approval-heading"><ClockCircleOutlined /><h3>等待你的审批</h3><Tag color="gold">{riskNames[String(p.risk)] ?? String(p.risk)}</Tag></div><p>{toolNames[String(p.tool)] ?? String(p.tool)} · <code>{String(p.tool)}</code></p><p>目标：<span className="file-path">{String(p.target)}</span></p>
      {tools.get(p.tool_call_id)?.[0] && <details><summary>查看调用内容</summary><pre>{JSON.stringify(tools.get(p.tool_call_id)![0].payload.input, null, 2)}</pre></details>}
      <p>持续授权范围：<span className="file-path">{String(p.always_scope_preview)}</span></p>
      <div className="card-actions">{decisions.map(([decision, label]) => <Button key={decision} type={decision === 'allow_once' ? 'primary' : 'default'} danger={decision.startsWith('reject')} disabled={busy || (decision.endsWith('always') && !canManage)} onClick={() => { if (decision.endsWith('always')) setPermanent({ call: p, decision }); else action(`/tool-approvals/${p.tool_call_id}`, { decision, input_hash: p.input_hash }) }}>{label}</Button>)}</div>{!canManage && <p>永久权限规则仅所有者或管理员可以保存。</p>}
    </section>
    if (event.type === 'question.requested' && !answered.has(p.request_id) && !terminal.has(event.task_run_id)) return <Question key={event.global_seq} event={event} action={action} busy={busy} />
    if (event.type === 'permission.requested' && resolved.has(p.tool_call_id)) {
      const decision = events.find((item) => item.type === 'permission.resolved' && item.payload.tool_call_id === p.tool_call_id)!
      return <details className="background-record" key={event.global_seq}><summary>审批记录<span>{decisions.find(([value]) => value === decision.payload.decision)?.[1]}</span></summary><p>工具：{String(p.tool)}</p><p className="file-path">{String(p.target)}</p><p className="file-path">持续授权范围：{String(p.always_scope_preview)}</p></details>
    }
    if (event.type === 'step.started') {
      const done = completedSteps.has(p.step_id)
      const requestedTools = [...tools.values()].filter((calls) => calls[0].payload.step_id === p.step_id)
      const modelCall = events.find((item) => item.type === 'llm.request_started' && item.payload.step_id === p.step_id)
      return <div className="execution-step" key={event.global_seq}>{done ? <CheckCircleOutlined /> : terminal.has(event.task_run_id) ? <ClockCircleOutlined /> : <LoadingOutlined />}<div><strong>{requestedTools.length ? requestedTools.map((calls) => toolNames[String(calls[0].payload.tool_name)] ?? String(calls[0].payload.tool_name)).join('、') : p.model_slot === 'aux' ? '整理执行结果' : '理解任务与组织回复'}</strong><small>{done ? '模型回合已完成' : terminal.has(event.task_run_id) ? '已结束' : '正在处理'} · 第 {String(p.ordinal)} 步{modelCall?.payload.model ? ` · ${String(modelCall.payload.model)}` : ''}</small></div></div>
    }
    if (event.type === 'tool.prepared') {
      const updates = tools.get(p.call_id)!
      const latest = updates[updates.length - 1]
      const input = p.input as Record<string, unknown>
      const status = toolStatusNames[latest.type]
      return <details className="tool-record" key={event.global_seq}><summary><FileTextOutlined /><span>{toolNames[String(p.tool_name)] ?? String(p.tool_name)}</span><Tag color={latest.type === 'tool.failed' ? 'red' : latest.type === 'tool.completed' ? 'green' : 'blue'}>{status}</Tag></summary>{toolTarget(input) && <p className="file-path">{toolTarget(input)}</p>}<p>工具：<code>{String(p.tool_name)}</code></p>
        <details><summary>调用参数</summary><pre>{JSON.stringify(p.input, null, 2)}</pre></details>
        {updates.filter((e) => e.payload.output_summary || e.payload.error || e.payload.artifact_path).map((e) => <div key={e.global_seq}><pre>{String(e.payload.output_summary ?? e.payload.error ?? '')}</pre>{e.payload.artifact_path ? <p>工件：{String(e.payload.artifact_path)}</p> : null}</div>)}
      </details>
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
  })}<Modal title="确认永久权限规则" open={Boolean(permanent)} okText="确认保存永久规则" cancelText="取消决定" confirmLoading={busy} onCancel={() => setPermanent(null)} onOk={() => { if (permanent) { action(`/tool-approvals/${permanent.call.tool_call_id}`, { decision: permanent.decision, input_hash: permanent.call.input_hash }); setPermanent(null) } }}><p>工具：{String(permanent?.call.tool)}</p><p>持续授权范围：{String(permanent?.call.always_scope_preview)}</p><p>{permanent?.decision === 'allow_always' ? '在此工具和资源范围保存允许规则。后续调用仍须满足当前文件访问范围、员工授权与受保护资源限制。' : '在此工具和资源范围保存拒绝规则，后续匹配调用将被拒绝。'}</p></Modal></div>
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
