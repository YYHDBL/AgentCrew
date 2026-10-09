import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Button, Modal } from 'antd'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { api, ApiFailure, connection, type Conversation } from '../session'
import { allPages, type Diagnostic, type Identity, type Resource } from '../governance-data'
import { parseEventFrame } from '../api/events'
import type { CronCreateRequest, CronJob, CronOccurrence, ScheduleProposal } from '../api/types'

type Authorization = CronJob['metadata']['pre_authorized'][number]
type Schedule = CronCreateRequest['schedule']
type Preview = { candidates: Authorization[]; next_run_at: number | null; authorization_sha256: string; selected: Authorization[] }
type ProposalDecision = 'allow_once' | 'reject_once'

interface Draft {
  id: string | null
  revision: number
  name: string
  kind: Schedule['kind']
  at: string
  every: string
  expr: string
  tz: string
  agent: string
  instruction: string
  mode: 'existing' | 'new_conversation'
  conversation: string
  selected: Authorization[]
  enabled: boolean
}

const statusText: Record<string, string> = {
  fired: '已触发', missed: '已错过', skipped: '已跳过', failed: '失败', interrupted: '已中断',
  pending_verification: '待核验', retry_wait: '等待重试', completed: '已完成', cancelled: '已取消',
  pending: '等待真人决定', approved: '已批准', rejected: '已拒绝', expired: '已失效'
}
const dateTime = (value: number | null, tz?: string): string => value === null ? '无' : new Date(value).toLocaleString('zh-CN', tz ? { timeZone: tz } : undefined)

function zonedInput(value: number | null, tz: string): string {
  if (value === null) return ''
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(value)
  const part = (name: string): string => parts.find((item) => item.type === name)?.value ?? ''
  return `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}:${part('second')}`
}

function epochInZone(value: string, tz: string): number {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value)
  if (!match) throw new Error('请输入有效的计划时间。')
  const target = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5]), Number(match[6] ?? 0))
  let candidate = target
  for (let attempt = 0; attempt < 3; attempt += 1) candidate += target - Date.parse(`${zonedInput(candidate, tz).replace('T', ' ')}Z`)
  if (zonedInput(candidate, tz) !== value.padEnd(19, ':00')) throw new Error('该当地时间在所选时区不存在，请选择有效时刻。')
  return candidate
}

function defaultDraft(workspace: string, agent: string): Draft {
  return { id: null, revision: 0, name: '', kind: 'every', at: '', every: '86400', expr: '0 9 * * 1-5', tz: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', agent,
    instruction: '', mode: 'new_conversation', conversation: '', selected: [], enabled: true }
}

function scheduleOf(draft: Draft): Schedule {
  if (draft.kind === 'at') return { kind: 'at', at_ms: epochInZone(draft.at, draft.tz), tz: draft.tz }
  if (draft.kind === 'every') return { kind: 'every', every_ms: Number(draft.every) * 1000, tz: draft.tz }
  return { kind: 'cron', expr: draft.expr, tz: draft.tz }
}

function occurrenceRun(row: CronOccurrence): string | null { return row.task_run_id ?? row.attempts.find((attempt) => attempt.task_run_id)?.task_run_id ?? null }

export function Automation({ identity, workspace, agent, revision, diagnostic, selectScope, onRun, onConversation }: {
  identity: Identity; workspace: string; agent: string; revision: number; diagnostic: Diagnostic
  selectScope: (workspace: string, agent: string) => void; onRun: (taskRunId: string) => void; onConversation: (conversationId: string) => void
}): JSX.Element {
  const [workspaces, setWorkspaces] = useState<Resource[]>([])
  const [agents, setAgents] = useState<Resource[]>([])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [jobs, setJobs] = useState<CronJob[]>([])
  const [history, setHistory] = useState<Record<string, CronOccurrence[]>>({})
  const [proposals, setProposals] = useState<ScheduleProposal[]>([])
  const [choices, setChoices] = useState<Record<string, Authorization[]>>({})
  const [draft, setDraft] = useState<Draft | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [streamStatus, setStreamStatus] = useState('正在连接发生与提案记录')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [streamRefresh, setStreamRefresh] = useState(0)
  const generation = useRef(0)
  const newPlanTrigger = useRef<HTMLButtonElement>(null)
  const planNameInput = useRef<HTMLInputElement>(null)
  const wasDraftOpen = useRef(false)
  const draftOpen = Boolean(draft)
  const identityKey = `${identity.effective_user_id}:${identity.role}:${identity.organization_status}`
  const readonly = diagnostic.mode !== 'normal' || identity.organization_status !== 'active' || identity.role === 'member'

  useLayoutEffect(() => {
    generation.current += 1
    setWorkspaces([]); setAgents([])
    setJobs([]); setHistory({}); setProposals([]); setChoices({}); setConversations([])
    setDraft(null); setPreview(null); setError(''); setNote('')
  }, [workspace, agent, identityKey, diagnostic.mode])

  useLayoutEffect(() => {
    if (draftOpen && !wasDraftOpen.current) planNameInput.current?.focus()
    if (wasDraftOpen.current && !draftOpen) requestAnimationFrame(() => newPlanTrigger.current?.focus())
    wasDraftOpen.current = draftOpen
  }, [draftOpen])

  useEffect(() => {
    const request = generation.current
    const controller = new AbortController()
    setJobs([]); setHistory({}); setError('')
    void (async () => {
      const [spaceRows, employeeRows, conversationRows] = await Promise.all([
        allPages<Resource>('/workspaces', controller.signal), allPages<Resource>('/agents', controller.signal), api<Conversation[]>('/conversations', undefined, controller.signal)
      ])
      if (controller.signal.aborted || request !== generation.current) return
      setWorkspaces(spaceRows.filter((row) => row.status === 'active'))
      const availableAgents = employeeRows.filter((row) => row.status === 'active' && row.workspace_id === workspace)
      setAgents(availableAgents)
      if (!availableAgents.some((row) => row.id === agent)) {
        selectScope(workspace, availableAgents[0]?.id ?? '')
        return
      }
      setConversations(conversationRows.filter((row) => row.workspace_id === workspace))
      const allJobs = await allPages<CronJob>(`/cron/jobs?workspace_id=${encodeURIComponent(workspace)}`, controller.signal)
      const visible = allJobs.filter((job) => job.metadata.agent_id === agent)
      const histories = await Promise.all(visible.map(async (job) => [job.id, await allPages<CronOccurrence>(`/cron/jobs/${job.id}/runs?limit=200`, controller.signal)] as const))
      if (controller.signal.aborted || request !== generation.current) return
      setJobs(visible); setHistory(Object.fromEntries(histories))
    })().catch((reason: Error) => {
      if (controller.signal.aborted || request !== generation.current) return
      setError(reason.message)
      if (reason instanceof ApiFailure && [401, 403].includes(reason.status)) { setWorkspaces([]); setAgents([]); setJobs([]); setHistory({}); setProposals([]); setChoices({}); setDraft(null); setPreview(null); setConversations([]) }
    })
    return () => controller.abort()
  }, [workspace, agent, identityKey, diagnostic.mode, revision, refresh])

  useEffect(() => {
    if (diagnostic.mode !== 'normal' || identity.organization_status !== 'active') return
    const request = generation.current
    const lifetime = new AbortController()
    let subscription: AbortController | undefined
    let retry: ReturnType<typeof setTimeout> | undefined
    let cursor = 0
    const loadProposal = async (id: string): Promise<void> => {
      try {
        const proposal = await api<ScheduleProposal>(`/cron/proposals/${id}`, undefined, lifetime.signal)
        if (lifetime.signal.aborted || request !== generation.current || proposal.proposal.workspace_id !== workspace || proposal.proposal.agent_id !== agent) return
        setProposals((previous) => {
          const current = previous.find((item) => item.id === proposal.id)
          return current && current.revision > proposal.revision ? previous : [proposal, ...previous.filter((item) => item.id !== proposal.id)]
        })
        setChoices((previous) => previous[proposal.id] ? previous : { ...previous, [proposal.id]: proposal.selected })
      } catch (reason) {
        if (lifetime.signal.aborted || request !== generation.current) return
        if (reason instanceof ApiFailure && [401, 403].includes(reason.status)) {
          setWorkspaces([]); setAgents([]); setJobs([]); setHistory({}); setProposals([]); setChoices({}); setDraft(null); setPreview(null); setConversations([]); setError(reason.message)
          lifetime.abort()
        } else if (!(reason instanceof ApiFailure && reason.status === 404)) setError(reason instanceof Error ? reason.message : String(reason))
      }
    }
    const listen = async (): Promise<void> => {
      try {
        const current = await connection()
        if (lifetime.signal.aborted || request !== generation.current) return
        subscription = new AbortController()
        await fetchEventSource(`${current.url}/api/cron/stream?${new URLSearchParams({ workspace_id: workspace, from: String(cursor) })}`, {
          headers: current.headers, signal: subscription.signal, openWhenHidden: true,
          async onopen(response) {
            if (!response.ok) {
              if ([401, 403].includes(response.status)) {
                setWorkspaces([]); setAgents([]); setJobs([]); setHistory({}); setProposals([]); setChoices({}); setConversations([]); setDraft(null); setPreview(null)
                lifetime.abort()
              }
              throw new Error(`自动化事件订阅失败（${response.status}）`)
            }
            if (!response.headers.get('content-type')?.startsWith('text/event-stream')) throw new Error(`自动化事件订阅失败（${response.status}）`)
            if (!lifetime.signal.aborted && request === generation.current) setStreamStatus('事件已连接')
          },
          onmessage(message) {
            if (lifetime.signal.aborted || request !== generation.current || !message.data || message.event === 'ping') return
            if (message.event === 'shutdown' || message.event === 'resync') { cursor = 0; subscription?.abort(); setRefresh((value) => value + 1); return }
            const event = parseEventFrame(message.data)
            if (event === null || !Number.isSafeInteger(event.global_seq) || event.global_seq <= cursor) return
            cursor = event.global_seq
            if (event.type === 'cron.proposal_requested' || event.type === 'cron.proposal_resolved') {
              const id = (event.payload as { proposal_id?: unknown } | undefined)?.proposal_id
              if (typeof id === 'string') void loadProposal(id)
            }
            if (event.type.startsWith('cron.job_')) setRefresh((value) => value + 1)
          },
          onerror(reason) { throw reason },
          onclose() { throw new Error('自动化事件连接已关闭') }
        })
      } catch (reason) {
        if (!lifetime.signal.aborted && request === generation.current) {
          try { await api(`/cron/jobs?workspace_id=${encodeURIComponent(workspace)}&limit=1`) }
          catch (accessError) {
            if (accessError instanceof ApiFailure && [401, 403].includes(accessError.status)) {
              setWorkspaces([]); setAgents([]); setJobs([]); setHistory({}); setProposals([]); setChoices({}); setConversations([]); setDraft(null); setPreview(null); setError(accessError.message); lifetime.abort()
            }
          }
          if (!lifetime.signal.aborted) setStreamStatus(`事件重新连接中：${reason instanceof Error ? reason.message : String(reason)}`)
        }
      }
      if (!lifetime.signal.aborted && request === generation.current) retry = setTimeout(() => void listen(), 1000)
    }
    void listen()
    return () => { lifetime.abort(); subscription?.abort(); clearTimeout(retry) }
  }, [workspace, agent, identityKey, diagnostic.mode, streamRefresh])

  const refreshData = (): void => { setRefresh((value) => value + 1); setStreamRefresh((value) => value + 1) }
  const openNew = (): void => { setError(''); setNote(''); setPreview(null); setDraft(defaultDraft(workspace, agent)) }
  const openEdit = (job: CronJob): void => {
    const schedule = job.schedule
    setError(''); setNote(''); setPreview(null)
    setDraft({ id: job.id, revision: job.revision, name: job.name, kind: schedule.kind,
      at: schedule.kind === 'at' ? zonedInput(schedule.at_ms, schedule.tz) : '', every: schedule.kind === 'every' ? String(schedule.every_ms / 1000) : '86400',
      expr: schedule.kind === 'cron' ? schedule.expr : '0 9 * * 1-5', tz: schedule.tz, agent: job.metadata.agent_id, instruction: job.target.instruction,
      mode: job.target.execution_mode, conversation: job.target.conversation_id ?? '', selected: job.metadata.pre_authorized, enabled: job.state.enabled })
  }

  const requestFor = (value: Draft): CronCreateRequest => ({ change_id: value.id ? `edit-${value.id}-${crypto.randomUUID()}` : crypto.randomUUID(), workspace_id: workspace,
    agent_id: value.agent, name: value.name.trim(), schedule: scheduleOf(value), target: { instruction: value.instruction, execution_mode: value.mode, conversation_id: value.mode === 'existing' ? value.conversation : null }, pre_authorized: value.selected, enabled: value.enabled })

  const checkPreview = async (): Promise<void> => {
    if (!draft) return
    const requestGeneration = generation.current
    setBusy(true); setError(''); setNote('')
    try {
      const request = requestFor(draft)
      const value = await api<Preview>('/cron/authorization-preview', { ...request, pre_authorized: [] })
      if (requestGeneration !== generation.current) return
      const allowed = draft.selected.filter((entry) => value.candidates.some((candidate) => candidate.tool === entry.tool && candidate.pattern === entry.pattern))
      setDraft({ ...draft, selected: allowed }); setPreview(value)
    } catch (reason) {
      if (requestGeneration === generation.current) setError(reason instanceof Error ? reason.message : String(reason))
    } finally { if (requestGeneration === generation.current) setBusy(false) }
  }

  const saveDraft = async (): Promise<void> => {
    if (!draft) return
    const requestGeneration = generation.current
    setBusy(true); setError(''); setNote('')
    try {
      const request = requestFor(draft)
      const actualPreview = await api<Preview>('/cron/authorization-preview', { ...request, pre_authorized: draft.selected })
      if (requestGeneration !== generation.current) return
      if (draft.id) {
        const { change_id, enabled, name, schedule, target } = request
        const pre_authorized = actualPreview.selected
        await api(`/cron/jobs/${draft.id}`, { change_id, expected_revision: draft.revision, name, schedule, target, pre_authorized, enabled }, undefined, 'PATCH')
      } else {
        await api('/cron/jobs', { ...request, pre_authorized: actualPreview.selected })
      }
      if (requestGeneration !== generation.current) return
      setDraft(null); setPreview(null); setNote('计划已由服务端保存。'); refreshData()
    } catch (reason) { if (requestGeneration === generation.current) setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { if (requestGeneration === generation.current) setBusy(false) }
  }

  const patchJob = async (job: CronJob, body: Record<string, unknown>, success: string): Promise<void> => {
    const requestGeneration = generation.current
    setBusy(true); setError(''); setNote('')
    try { await api(`/cron/jobs/${job.id}`, { change_id: crypto.randomUUID(), expected_revision: job.revision, ...body }, undefined, 'PATCH'); if (requestGeneration !== generation.current) return; setNote(success); refreshData() }
    catch (reason) { if (requestGeneration !== generation.current) return; setError(reason instanceof Error ? reason.message : String(reason)); refreshData() }
    finally { if (requestGeneration === generation.current) setBusy(false) }
  }

  const deleteJob = async (job: CronJob): Promise<void> => {
    const requestGeneration = generation.current
    setBusy(true); setError(''); setNote('')
    try { await api(`/cron/jobs/${job.id}`, { change_id: crypto.randomUUID(), expected_revision: job.revision }, undefined, 'DELETE'); if (requestGeneration !== generation.current) return; setNote('计划已删除；已派发任务和历史发生记录仍保留。'); refreshData() }
    catch (reason) { if (requestGeneration !== generation.current) return; setError(reason instanceof Error ? reason.message : String(reason)); refreshData() }
    finally { if (requestGeneration === generation.current) setBusy(false) }
  }

  const runNow = async (job: CronJob): Promise<void> => {
    const requestGeneration = generation.current
    setBusy(true); setError(''); setNote('')
    try {
      const occurrence = await api<CronOccurrence>(`/cron/jobs/${job.id}/run-now`, { client_request_id: crypto.randomUUID(), expected_revision: job.revision })
      if (requestGeneration !== generation.current) return
      setNote(`已提交独立手动发生 ${occurrence.id}，仍按无人值守权限执行。`); refreshData()
    } catch (reason) { if (requestGeneration !== generation.current) return; setError(reason instanceof Error ? reason.message : String(reason)); refreshData() }
    finally { if (requestGeneration === generation.current) setBusy(false) }
  }

  const openRecovery = async (taskRunId: string): Promise<void> => {
    const requestGeneration = generation.current
    try {
      const task = await api<{ conversation_id: string }>(`/task-runs/${taskRunId}`)
      if (requestGeneration === generation.current) onConversation(task.conversation_id)
    } catch (reason) {
      if (requestGeneration === generation.current) setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const decide = async (proposal: ScheduleProposal, decision: ProposalDecision): Promise<void> => {
    const requestGeneration = generation.current
    setBusy(true); setError(''); setNote('')
    try {
      const selected = decision === 'allow_once' ? choices[proposal.id] ?? [] : []
      const updated = await api<ScheduleProposal>(`/cron/proposals/${proposal.id}`, { decision, input_hash: proposal.input_hash, expected_revision: proposal.revision, selected })
      if (requestGeneration !== generation.current) return
      setProposals((items) => [updated, ...items.filter((item) => item.id !== updated.id)])
      setNote(decision === 'allow_once' ? `提案已批准，计划 ${updated.job_id} 已创建。` : '提案已拒绝，没有创建计划。')
      refreshData()
    } catch (reason) {
      if (requestGeneration !== generation.current) return
      setError(reason instanceof Error ? reason.message : String(reason))
      try {
        const actual = await api<ScheduleProposal>(`/cron/proposals/${proposal.id}`)
        if (requestGeneration === generation.current) setProposals((items) => [actual, ...items.filter((item) => item.id !== actual.id)])
      } catch (readError) {
        if (requestGeneration === generation.current && readError instanceof ApiFailure && [401, 403].includes(readError.status)) { setWorkspaces([]); setAgents([]); setJobs([]); setHistory({}); setProposals([]); setChoices({}); setDraft(null); setPreview(null); setConversations([]) }
      }
    } finally { if (requestGeneration === generation.current) setBusy(false) }
  }

  const toggleChoice = (proposal: ScheduleProposal, value: Authorization): void => {
    setChoices((previous) => {
      const selected = previous[proposal.id] ?? proposal.selected
      const exists = selected.some((item) => item.tool === value.tool && item.pattern === value.pattern)
      return { ...previous, [proposal.id]: exists ? selected.filter((item) => item.tool !== value.tool || item.pattern !== value.pattern) : [...selected, value] }
    })
  }

  return <main className="automation-page" aria-busy={busy}>
    <header className="automation-heading"><div><h1>自动化</h1><p>管理真实计划、员工创建审批与每次发生记录。{streamStatus}</p></div><div className="automation-actions"><Button disabled={busy} onClick={refreshData}>刷新实际记录</Button><Button ref={newPlanTrigger} type="primary" disabled={readonly || busy || identity.role === 'member'} onClick={openNew}>新建计划</Button></div></header>
    <div className="automation-scope">
      <label>工作区<select aria-label="自动化工作区" value={workspace} disabled={busy} onChange={(event) => selectScope(event.target.value, '')}>{workspaces.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
      <label>员工<select aria-label="自动化员工" value={agent} disabled={busy} onChange={(event) => selectScope(workspace, event.target.value)}>{agents.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
      <span>当前身份：{identity.name}（{identity.role}）</span>
    </div>
    {diagnostic.mode !== 'normal' && <p className="automation-warning" role="alert">只读诊断期间，计划创建、编辑和审批操作已禁用。</p>}
    {error && <p className="automation-error" role="alert">{error}；草稿和已加载的合法选项仍保留。</p>}{note && <p className="automation-note" role="status">{note}</p>}
    <section aria-labelledby="automation-proposals-heading"><h2 id="automation-proposals-heading">员工创建审批</h2>
      {proposals.filter((proposal) => proposal.status === 'pending').map((proposal) => <article className="automation-proposal" key={proposal.id}>
        <header><div><h3>{proposal.proposal.name}</h3><p>员工 {proposal.proposal.agent_id} · TaskRun {proposal.task_run_id} · attempt {proposal.attempt_no} · call {proposal.call_id}</p></div><span>{statusText[proposal.status]}</span></header>
        <dl><dt>原始提案</dt><dd><pre>{JSON.stringify(proposal.proposal, null, 2)}</pre></dd><dt>原始参数哈希</dt><dd>{proposal.input_hash}</dd><dt>合法候选范围</dt><dd>{proposal.candidates.length ? proposal.candidates.map((value) => <label className="automation-choice" key={`${value.tool}:${value.pattern}`}><input type="checkbox" disabled={busy || readonly} checked={(choices[proposal.id] ?? proposal.selected).some((item) => item.tool === value.tool && item.pattern === value.pattern)} onChange={() => toggleChoice(proposal, value)} />{value.tool} · {value.pattern}</label>) : <p>服务端没有提供可选预授权范围。</p>}</dd></dl>
        <p>范围选择只保存在本次决定中，不修改上方模型原始提案。永久允许规则不能代替真人创建批准。</p>
        <div className="automation-actions"><Button type="primary" disabled={busy || readonly} onClick={() => void decide(proposal, 'allow_once')}>批准并创建唯一计划</Button><Button danger disabled={busy || readonly} onClick={() => void decide(proposal, 'reject_once')}>拒绝提案</Button></div>
      </article>)}
      {!proposals.some((proposal) => proposal.status === 'pending') && <p className="automation-empty">当前范围没有待处理的员工创建提案。</p>}
      {proposals.some((proposal) => proposal.status !== 'pending') && <details className="automation-job"><summary>已决定或已失效提案（{proposals.filter((proposal) => proposal.status !== 'pending').length}）</summary>{proposals.filter((proposal) => proposal.status !== 'pending').map((proposal) => <p key={proposal.id}>{proposal.proposal.name} · {statusText[proposal.status] ?? proposal.status} · 员工 {proposal.proposal.agent_id} · 提案 {proposal.id}{proposal.job_id ? ` · 计划 ${proposal.job_id}` : ''} · 决定者 {proposal.decided_by ?? '服务端'} · {proposal.decided_at ?? '时间未提供'}</p>)}</details>}
    </section>
    <section aria-labelledby="automation-jobs-heading"><h2 id="automation-jobs-heading">计划与发生记录</h2>
      {jobs.length === 0 ? <p className="automation-empty">当前范围没有可见计划。</p> : jobs.map((job) => <article className="automation-job" key={job.id}>
        <header><div><h3>{job.name}</h3><p>{job.id} · 创建身份 {job.metadata.created_by === 'agent' ? `员工 ${job.metadata.agent_id}` : `真人 ${job.metadata.owner_id}`} · {job.metadata.created_via_task_run_id ? `来源 TaskRun ${job.metadata.created_via_task_run_id}` : '人工创建'}</p></div><strong>{job.state.enabled ? '启用' : '停用'}</strong></header>
        <p>{job.schedule.kind === 'at' ? `一次性时刻 ${dateTime(job.schedule.at_ms, job.schedule.tz)}` : job.schedule.kind === 'every' ? `每 ${job.schedule.every_ms / 1000} 秒` : `cron ${job.schedule.expr}`} · 时区 {job.schedule.tz} · 下次运行 {dateTime(job.state.next_run_at, job.schedule.tz)} · 最近状态 {statusText[job.state.last_status ?? ''] ?? '尚未运行'} · 自动重试 {job.state.retry_count}/{job.state.max_retries}</p>
        <p>员工 {job.metadata.agent_id} · {job.target.execution_mode === 'existing' ? `现有会话 ${job.target.conversation_id}` : '新建会话'} · {job.target.instruction}</p>
        <p>预授权范围：{job.metadata.pre_authorized.length ? job.metadata.pre_authorized.map((item) => `${item.tool} · ${item.pattern}`).join('；') : '无'}</p>
        <div className="automation-actions"><Button disabled={readonly || busy} onClick={() => openEdit(job)}>编辑</Button><Button disabled={readonly || busy} onClick={() => void patchJob(job, { enabled: !job.state.enabled }, job.state.enabled ? '计划已停用；已派发任务继续按服务端状态处理，未来 occurrence 不再派发。' : '计划已启用。')}>{job.state.enabled ? '停用' : '启用'}</Button><Button disabled={readonly || busy} onClick={() => void runNow(job)}>手动运行</Button><Button danger disabled={readonly || busy} onClick={() => Modal.confirm({ title: '删除计划', content: '删除后不再派发未来 occurrence；已派发任务和发生历史保留，停用会阻止自动重试。', okText: '确认删除', cancelText: '取消删除', onOk: () => deleteJob(job) })}>删除</Button></div>
        <details><summary>查看发生历史（{(history[job.id] ?? []).length}）</summary>{(history[job.id] ?? []).length ? <ol>{(history[job.id] ?? []).map((occurrence) => <li key={occurrence.id}><strong>{statusText[occurrence.status] ?? occurrence.status}</strong> · {occurrence.trigger === 'manual' ? `独立手动发生 ${dateTime(occurrence.triggered_at, job.schedule.tz)}` : `计划时刻 ${dateTime(occurrence.scheduled_at, job.schedule.tz)}`} · occurrence {occurrence.id} · 额外重试 {occurrence.retry_count}/{job.state.max_retries}{occurrence.retry_at ? `，下次重试 ${dateTime(occurrence.retry_at, job.schedule.tz)}` : ''}{occurrence.note ? ` · ${occurrence.note}` : ''}{occurrence.attempts.map((attempt) => <div className="automation-attempt" key={`${occurrence.id}:${attempt.retry_no}`}><span>retry_no {attempt.retry_no} · TaskRun {attempt.task_run_id} · attempt_no {attempt.attempt_no} · {statusText[attempt.status] ?? attempt.status}</span><Button onClick={() => onRun(attempt.task_run_id)}>打开 TaskRun、报告与产物</Button>{['interrupted', 'pending_verification'].includes(occurrence.status) && <Button onClick={() => void openRecovery(attempt.task_run_id)}>进入工作台安全处理</Button>}</div>)}{occurrence.attempts.length === 0 && occurrenceRun(occurrence) && <><Button onClick={() => onRun(occurrenceRun(occurrence)!)}>打开 TaskRun、报告与产物</Button>{['interrupted', 'pending_verification'].includes(occurrence.status) && <Button onClick={() => void openRecovery(occurrenceRun(occurrence)!)}>进入工作台安全处理</Button>}</>}</li>)}</ol> : <p>尚无发生记录。</p>}</details>
        <p className="automation-impact">编辑会影响未来计划修订；已派发任务保留已有 TaskRun 和历史，旧修订失败不会自动重试。停用或删除会停止未来派发，并阻止自动重试。</p>
      </article>)}
    </section>
    <Modal title={draft?.id ? '编辑自动化计划' : '创建自动化计划'} open={Boolean(draft)} width={760} okText={draft?.id ? '保存修改' : '创建计划'} cancelText="取消编辑" confirmLoading={busy} okButtonProps={{ disabled: readonly || !preview }} onCancel={() => { if (!busy) setDraft(null) }} onOk={() => void saveDraft()} afterOpenChange={(open) => { if (open) requestAnimationFrame(() => planNameInput.current?.focus()) }} destroyOnClose>
      {draft && <div className="automation-form">
        <label>计划名称<input ref={planNameInput} aria-label="计划名称" disabled={busy || readonly} required maxLength={80} value={draft.name} onChange={(event) => { setDraft({ ...draft, name: event.target.value }); setPreview(null) }} /></label>
        <label>计划类型<select aria-label="计划类型" disabled={busy || readonly} value={draft.kind} onChange={(event) => { setDraft({ ...draft, kind: event.target.value as Schedule['kind'] }); setPreview(null) }}><option value="at">at 一次性</option><option value="every">every 固定间隔</option><option value="cron">cron 日历规则</option></select></label>
        {draft.kind === 'at' && <label>计划时间<input aria-label="计划时间" disabled={busy || readonly} type="datetime-local" step="1" value={draft.at} onChange={(event) => { setDraft({ ...draft, at: event.target.value }); setPreview(null) }} /></label>}
        {draft.kind === 'every' && <label>间隔秒数<input aria-label="间隔秒数" disabled={busy || readonly} type="number" min="1" step="1" value={draft.every} onChange={(event) => { setDraft({ ...draft, every: event.target.value }); setPreview(null) }} /></label>}
        {draft.kind === 'cron' && <label>cron 表达式<input aria-label="cron 表达式" disabled={busy || readonly} value={draft.expr} onChange={(event) => { setDraft({ ...draft, expr: event.target.value }); setPreview(null) }} /><small>使用五字段或末尾秒字段六字段表达式。</small></label>}
        <label>IANA 时区<input aria-label="IANA 时区" disabled={busy || readonly} value={draft.tz} onChange={(event) => { setDraft({ ...draft, tz: event.target.value }); setPreview(null) }} /></label>
        <label>员工<select aria-label="计划员工" disabled={busy || readonly || Boolean(draft.id)} value={draft.agent} onChange={(event) => { setDraft({ ...draft, agent: event.target.value, conversation: '' }); setPreview(null) }}>{agents.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
        {draft.id && <p>已存在计划的员工身份固定；如需更换员工，请另建计划并重新核查授权范围。</p>}
        <label>执行会话<select aria-label="执行会话模式" disabled={busy || readonly} value={draft.mode} onChange={(event) => { setDraft({ ...draft, mode: event.target.value as Draft['mode'], conversation: '', selected: [] }); setPreview(null) }}><option value="new_conversation">新建会话</option><option value="existing">使用现有会话</option></select></label>
        {draft.mode === 'existing' && <label>现有会话<select aria-label="现有会话" disabled={busy || readonly} value={draft.conversation} onChange={(event) => { setDraft({ ...draft, conversation: event.target.value }); setPreview(null) }}><option value="">选择可见会话</option>{conversations.filter((row) => row.agent_id === draft.agent).map((row) => <option key={row.id} value={row.id}>{row.title || row.id} · {row.id}</option>)}</select></label>}
        <label>执行指令<textarea aria-label="执行指令" disabled={busy || readonly} rows={5} value={draft.instruction} onChange={(event) => { setDraft({ ...draft, instruction: event.target.value }); setPreview(null) }} /></label>
        <label className="automation-checkbox"><input type="checkbox" disabled={busy || readonly} checked={draft.enabled} onChange={(event) => { setDraft({ ...draft, enabled: event.target.checked }); setPreview(null) }} />创建后启用</label>
        <Button disabled={busy || readonly || !draft.name.trim() || !draft.instruction.trim() || (draft.mode === 'existing' && !draft.conversation)} onClick={() => void checkPreview()}>核查服务端候选范围与下一次时间</Button>
        {preview && <section className="automation-preview"><h3>服务端预览</h3><p>下一次运行：{dateTime(preview.next_run_at)} · 授权摘要 SHA256：{preview.authorization_sha256}</p>{preview.candidates.length ? preview.candidates.map((candidate) => <label className="automation-choice" key={`${candidate.tool}:${candidate.pattern}`}><input type="checkbox" disabled={busy || readonly} checked={draft.selected.some((item) => item.tool === candidate.tool && item.pattern === candidate.pattern)} onChange={() => { const exists = draft.selected.some((item) => item.tool === candidate.tool && item.pattern === candidate.pattern); setDraft({ ...draft, selected: exists ? draft.selected.filter((item) => item.tool !== candidate.tool || item.pattern !== candidate.pattern) : [...draft.selected, candidate] }) }} />{candidate.tool} · {candidate.pattern}</label>) : <p>当前授权没有可选择的预授权范围。</p>}</section>}
        {error && <p role="alert">{error}</p>}
      </div>}
    </Modal>
  </main>
}
