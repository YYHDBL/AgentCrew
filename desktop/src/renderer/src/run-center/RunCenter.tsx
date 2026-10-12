import { useEffect, useRef, useState } from 'react'
import { Button, Modal } from 'antd'
import { api, ApiFailure, type Frame } from '../session'
import { parseEventFrame } from '../api/events'
import type { Identity, RunAttempts, RunCall, RunEventsPage, RunMetrics, RunRecord, TraceJob, TraceReport } from '../api/types'
import { Replay } from './Replay'

type RunPage = { items: RunRecord[]; next_after: string | null; at_global_seq: number }
type ReportResponse = { report: TraceReport | null; job: TraceJob | null; applicable: boolean }
type ReplayRange = { head: number; start: number; end: number; attempt: string }
const states: Record<string, string> = { queued: '排队中', running: '执行中', waiting_user: '等待人工', waiting_verification: '待核验',
  interrupted: '已中断', completed: '已完成', failed: '失败', cancelled: '已取消' }

function frames(items: RunEventsPage['items']): Frame[] {
  return items.map((item) => parseEventFrame(JSON.stringify(item))).filter((item): item is Frame => item !== null)
}

export function RunCenter({ identity, revision, initialTask }: { identity: Identity; revision: number; initialTask: string | null }): JSX.Element {
  const [workspace, setWorkspace] = useState('')
  const [agent, setAgent] = useState('')
  const [status, setStatus] = useState('')
  const [source, setSource] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [rows, setRows] = useState<RunRecord[]>([])
  const [after, setAfter] = useState<string | null>(null)
  const [listBusy, setListBusy] = useState(false)
  const [metrics, setMetrics] = useState<RunMetrics | null>(null)
  const [taskId, setTaskId] = useState(initialTask ?? sessionStorage.getItem('run-center-task') ?? '')
  const [inputId, setInputId] = useState(taskId)
  const [record, setRecord] = useState<RunRecord | null>(null)
  const [attempts, setAttempts] = useState<RunAttempts>([])
  const [attempt, setAttempt] = useState(() => {
    const saved = sessionStorage.getItem(`replay-range:${identity.effective_user_id}:${taskId}`)
    return saved === null ? '' : (JSON.parse(saved) as ReplayRange).attempt
  })
  const [events, setEvents] = useState<Frame[]>([])
  const [head, setHead] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [position, setPosition] = useState(() => Number(sessionStorage.getItem(`replay-position:${taskId}`) ?? 0))
  const [report, setReport] = useState<ReportResponse | null>(null)
  const [call, setCall] = useState<RunCall | null>(null)
  const [tail, setTail] = useState<Frame[]>([])
  const [latestHead, setLatestHead] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [listError, setListError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [confirmSkill, setConfirmSkill] = useState(false)
  const operation = useRef(0)
  const listOperation = useRef(0)
  const listPending = useRef(false)
  const rangeKey = (id = taskId): string => `replay-range:${identity.effective_user_id}:${id}`
  const savedRange = (): ReplayRange | null => {
    const saved = sessionStorage.getItem(rangeKey())
    return saved === null ? null : JSON.parse(saved) as ReplayRange
  }
  const saveRange = (range: ReplayRange): void => { sessionStorage.setItem(rangeKey(), JSON.stringify(range)) }
  const filter = new URLSearchParams({ limit: '20', ...(workspace ? { workspace_id: workspace } : {}), ...(agent ? { agent_id: agent } : {}),
    ...(status ? { status } : {}), ...(source ? { source } : {}), ...(since ? { since: new Date(since).toISOString() } : {}), ...(until ? { until: new Date(until).toISOString() } : {}) }).toString()
  const changePosition = (seq: number): void => {
    setPosition(seq)
    sessionStorage.setItem(`replay-position:${taskId}`, String(seq))
  }
  const choose = (id: string): void => {
    const saved = sessionStorage.getItem(rangeKey(id))
    setTaskId(id); setInputId(id); setAttempt(saved === null ? '' : (JSON.parse(saved) as ReplayRange).attempt); setCall(null)
    setPosition(Number(sessionStorage.getItem(`replay-position:${id}`) ?? 0))
    sessionStorage.setItem('run-center-task', id)
  }
  useEffect(() => {
    if (initialTask) choose(initialTask)
  }, [initialTask])
  useEffect(() => {
    listOperation.current += 1
    const controller = new AbortController()
    setRows([]); setMetrics(null); setAfter(null); setListError('')
    void Promise.all([api<RunPage>(`/task-runs?${filter}`, undefined, controller.signal),
      api<RunMetrics>(`/runs/metrics?${filter}`, undefined, controller.signal)]).then(([page, statistics]) => {
      if (!controller.signal.aborted) { setRows(page.items); setAfter(page.next_after); setMetrics(statistics) }
    }).catch((reason: Error) => { if (!controller.signal.aborted) setListError(reason.message) })
    return () => controller.abort()
  }, [filter, identity.effective_user_id, revision, refresh])
  useEffect(() => {
    operation.current += 1
    const generation = operation.current
    const controller = new AbortController()
    setRecord(null); setEvents([]); setAttempts([]); setReport(null); setCall(null); setConfirmSkill(false); setTail([])
    if (!taskId) return
    setError('')
    setBusy(true)
    const saved = savedRange()
    void Promise.all([api<RunRecord>(`/task-runs/${taskId}`, undefined, controller.signal),
      api<RunAttempts>(`/task-runs/${taskId}/attempts`, undefined, controller.signal),
      api<ReportResponse>(`/task-runs/${taskId}/audit-report`, undefined, controller.signal),
      api<RunEventsPage>(`/task-runs/${taskId}/events?limit=10&after_seq=${saved?.start ?? 0}${saved?.head ? `&through_global_seq=${saved.head}` : ''}${attempt ? `&attempt_no=${attempt}` : ''}`, undefined, controller.signal)]).then(async ([task, history, analysis, first]) => {
      const loaded = frames(first.items)
      let page = first
      while (saved && page.has_more && (page.items.at(-1)?.seq ?? 0) < saved.end) {
        page = await api<RunEventsPage>(`/task-runs/${taskId}/events?limit=10&after_seq=${page.items.at(-1)?.seq ?? 0}&through_global_seq=${first.at_global_seq}${attempt ? `&attempt_no=${attempt}` : ''}`, undefined, controller.signal)
        loaded.push(...frames(page.items))
      }
      if (controller.signal.aborted || generation !== operation.current) return
      setRecord(task); setAttempts(history); setReport(analysis); setEvents(loaded); setHead(first.at_global_seq); setHasMore(page.has_more)
      if (saved && saved.start > 0 && !loaded.some((event) => event.seq === saved.start + 1)) setError(`目标事件 seq ${saved.start + 1} 在保存范围中缺失。`)
      setLatestHead(task.at_global_seq)
      saveRange({ head: first.at_global_seq, start: saved?.start ?? 0, end: page.items.at(-1)?.seq ?? 0, attempt })
      if (sessionStorage.getItem(`replay-position:${taskId}`) === null) changePosition(loaded.at(-1)?.seq ?? 0)
    }).catch((reason: Error) => {
      if (!controller.signal.aborted) {
        setError(reason.message)
        if (reason instanceof ApiFailure && [401, 403].includes(reason.status)) {
          sessionStorage.removeItem('run-center-task'); sessionStorage.removeItem(rangeKey()); sessionStorage.removeItem(`replay-position:${taskId}`); setTaskId('')
        }
      }
    }).finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => controller.abort()
  }, [taskId, attempt, identity.effective_user_id, revision, refresh])
  useEffect(() => {
    if (!record) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    let last = 0
    const poll = async (): Promise<void> => {
      try {
        const [actual, analysis] = await Promise.all([api<RunRecord>(`/task-runs/${taskId}`, undefined, controller.signal),
          api<ReportResponse>(`/task-runs/${taskId}/audit-report`, undefined, controller.signal)])
        let page: RunEventsPage
        const appended: Frame[] = []
        do {
          page = await api<RunEventsPage>(`/task-runs/${taskId}/events?limit=20&after_seq=${last}&after_global_seq=${head}&through_global_seq=${actual.at_global_seq}${attempt ? `&attempt_no=${attempt}` : ''}`, undefined, controller.signal)
          appended.push(...frames(page.items))
          last = page.items.at(-1)?.seq ?? last
        } while (page.has_more && !controller.signal.aborted)
        if (controller.signal.aborted) return
        setTail((current) => {
          const known = new Set(current.map((event) => event.global_seq))
          return [...current, ...appended.filter((event) => !known.has(event.global_seq))]
        }); setLatestHead(page.at_global_seq); setReport(analysis); setRecord(actual)
        if (['queued', 'running', 'waiting_user', 'waiting_verification'].includes(actual.status ?? '') || ['queued', 'running', 'cancelling'].includes(analysis.job?.status ?? '')) timer = setTimeout(() => void poll(), 2000)
      } catch (reason) {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : String(reason))
          if (reason instanceof ApiFailure && [401, 403].includes(reason.status)) { setRecord(null); setEvents([]); setTail([]); setReport(null); setCall(null) }
        }
      }
    }
    timer = setTimeout(() => void poll(), 2000)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [taskId, record !== null, head, attempt, identity.effective_user_id, revision, report?.job?.id])
  const moreRuns = async (): Promise<void> => {
    if (!after || listPending.current) return
    listPending.current = true; setListBusy(true)
    const generation = listOperation.current
    try {
      const page = await api<RunPage>(`/task-runs?${filter}&after=${encodeURIComponent(after)}`)
      if (generation === listOperation.current) { setRows((current) => [...current, ...page.items]); setAfter(page.next_after) }
    } catch (reason) { if (generation === listOperation.current) setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { listPending.current = false; setListBusy(false) }
  }
  const more = async (): Promise<void> => {
    if (busy || !taskId) return
    const generation = operation.current
    setBusy(true)
    try {
      const last = events.at(-1)?.seq ?? 0
      const page = await api<RunEventsPage>(`/task-runs/${taskId}/events?limit=10&after_seq=${last}&through_global_seq=${head}${attempt ? `&attempt_no=${attempt}` : ''}`)
      if (generation === operation.current) {
        setEvents((current) => [...current, ...frames(page.items)]); setHasMore(page.has_more)
        saveRange({ head, start: savedRange()?.start ?? 0, end: page.items.at(-1)?.seq ?? last, attempt })
      }
    } catch (reason) { if (generation === operation.current) setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { if (generation === operation.current) setBusy(false) }
  }
  const locate = async (): Promise<void> => {
    const reference = report?.report?.report.root_cause_event
    if (!reference) return
    const selectedAttempt = reference.attempt_no === null ? '' : String(reference.attempt_no)
    saveRange({ head: report!.report!.source_global_seq, start: Math.max(0, reference.seq - 1), end: reference.seq, attempt: selectedAttempt })
    setAttempt(selectedAttempt); changePosition(reference.seq); setRefresh((value) => value + 1)
  }
  const inspect = async (frame: Frame): Promise<void> => {
    const id = frame.type.startsWith('tool.') ? frame.payload.call_id : frame.payload.llm_call_id
    if (typeof id !== 'string') { setError('原始事件没有登记调用标识。'); return }
    const generation = operation.current
    try {
      const actual = await api<RunCall>(`/task-runs/${taskId}/calls/${encodeURIComponent(id)}`)
      if (generation === operation.current) setCall(actual)
    } catch (reason) { if (generation === operation.current) setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  const promote = async (): Promise<void> => {
    if (!report?.report) return
    const generation = operation.current
    setBusy(true)
    try {
      const job = await api<TraceJob>(`/audit-reports/${report.report.id}/promote-skill`, { confirmed: true,
        expected_report_id: report.report.id, client_request_id: crypto.randomUUID() })
      if (generation === operation.current) { setError(`固化作业 ${job.id} · ${job.status}，授权由管理操作单独授予。`); setConfirmSkill(false) }
    } catch (reason) { if (generation === operation.current) setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { if (generation === operation.current) setBusy(false) }
  }
  const restart = (): void => {
    saveRange({ head, start: 0, end: 0, attempt }); setRefresh((value) => value + 1)
  }
  const latest = (): void => {
    saveRange({ head: latestHead, start: 0, end: 0, attempt }); changePosition(0); setRefresh((value) => value + 1)
  }
  const visibleEvents = events.filter((event) => (event.seq ?? 0) <= position)
  const artifactEvents = visibleEvents.filter((event) => event.type.startsWith('artifact.'))
  const historical = hasMore || position < (events.at(-1)?.seq ?? 0)
  return <main className="run-center-page" aria-busy={busy}>
    <header className="run-center-heading"><div><h1>运行中心</h1><p>查询真实执行，核查事件来源与模型分析。</p></div><Button onClick={() => setRefresh((value) => value + 1)}>刷新实际记录</Button></header>
    <section className="run-filters" aria-label="运行筛选">
      <label>工作区<input aria-label="运行工作区" value={workspace} onChange={(event) => setWorkspace(event.target.value)} /></label>
      <label>员工<input aria-label="运行员工" value={agent} onChange={(event) => setAgent(event.target.value)} /></label>
      <label>状态<select aria-label="运行状态" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option>{Object.entries(states).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>来源<select aria-label="运行来源" value={source} onChange={(event) => setSource(event.target.value)}><option value="">全部来源</option><option value="user">用户任务</option><option value="cron">定时任务</option><option value="manual">手动计划</option></select></label>
      <label>开始时间<input aria-label="运行开始时间" type="datetime-local" value={since} onChange={(event) => setSince(event.target.value)} /></label>
      <label>结束时间<input aria-label="运行结束时间" type="datetime-local" value={until} onChange={(event) => setUntil(event.target.value)} /></label>
      <label>任务标识<input aria-label="任务标识" value={inputId} onChange={(event) => setInputId(event.target.value)} /></label><Button disabled={!inputId.trim()} onClick={() => choose(inputId.trim())}>打开任务记录</Button>
    </section>
    {metrics && <section className="run-metrics" aria-label="实际运行指标">
      <div><strong>{metrics.task_count}</strong><span>任务记录</span></div><div><strong>{metrics.completion_rate === null ? '未知' : `${(metrics.completion_rate * 100).toFixed(1)}%`}</strong><span>任务完成率</span></div>
      <div><strong>{metrics.llm_call_count}</strong><span>模型调用</span></div><div><strong>{metrics.tool_call_count}</strong><span>工具调用</span></div>
      <div><strong>{metrics.prompt_tokens === null ? '未知' : metrics.prompt_tokens}</strong><span>输入 tokens</span></div><div><strong>{metrics.completion_tokens === null ? '未知' : metrics.completion_tokens}</strong><span>输出 tokens</span></div>
      {!metrics.usage_complete && <p>缺失 usage：{metrics.missing_usage_calls} 项；已知部分 {metrics.known_prompt_tokens ?? '未知'} / {metrics.known_completion_tokens ?? '未知'}。</p>}
    </section>}
    {(error || listError) && <p role="alert">{error || listError}</p>}
    <div className="run-center-columns">
      <section className="run-index" aria-label="运行列表"><h2>执行记录</h2>{rows.map((row) => <button key={row.id} disabled={!row.id} aria-current={row.id === taskId ? 'true' : undefined} onClick={() => { if (row.id) choose(row.id) }}><strong>{row.instruction ?? '指令缺失'}</strong><small>{states[row.status ?? ''] ?? row.status} · {row.agent_id} · {row.source}</small><code>{row.id}</code></button>)}{!rows.length && <p>当前筛选没有可见的运行记录。</p>}<Button disabled={!after || listBusy} onClick={() => void moreRuns()}>加载更多运行</Button></section>
      <section className="run-record" aria-label="任务事件详情">{record ? <>
        <h2>任务与尝试</h2><code>{taskId}</code><p>{record.instruction}</p><p>{states[record.status ?? ''] ?? record.status} · {record.workspace_id} · {record.agent_id} · {record.source}</p>
        <label className="run-attempt">适用尝试<select aria-label="运行尝试" value={attempt} onChange={(event) => { saveRange({ head, start: 0, end: 0, attempt: event.target.value }); setAttempt(event.target.value); changePosition(0) }}><option value="">全部尝试</option>{attempts.map((item) => <option key={item.attempt_no} value={item.attempt_no}>{item.attempt_no} · {item.kind} · {item.outcome ?? '尚无终态'}</option>)}</select></label>
        <details><summary>尝试与冻结配置来源</summary><pre className="run-call-output">{JSON.stringify({ agent_spec_snapshot: record.agent_spec_snapshot, skill_versions: record.skill_versions, attempts }, null, 2)}</pre></details>
        <Replay events={events} position={position} selected={events.at(-1)?.seq ?? 0} hasMore={hasMore} busy={busy} change={changePosition} more={more} restart={restart} />
        <section aria-label="实时事件尾部"><p>历史水位 {head} · 当前提交水位 {latestHead} · 新增事件 {tail.length}</p>{latestHead > head && <Button onClick={latest}>读取当前事件范围</Button>}{tail.length > 0 && <details><summary>新增原始事件</summary><pre className="run-call-output">{JSON.stringify(tail, null, 2)}</pre></details>}</section>
        <section className="run-analysis"><h2>模型分析报告</h2>{report?.report ? <><p className="run-muted">辅助模型：{report.report.model} · {new Date(report.report.created_at).toLocaleString('zh-CN')} · 输入水位 {report.report.source_global_seq} · 尝试 {report.report.attempt_no ?? '未开始'} · {report.applicable ? '适用于当前事件水位' : '历史分析，当前记录已经变化'}</p><p>{report.report.report.summary}</p><p>{report.report.report.root_cause ?? '模型没有确认根因。'}</p><Button disabled={!report.report.report.root_cause_event} onClick={() => void locate()}>定位原始事件</Button><details><summary>完整模型分析与引用</summary><pre className="run-call-output">{JSON.stringify(report.report.report, null, 2)}</pre></details>{report.report.report.skill_proposal && <><h3>技能建议：{report.report.report.skill_proposal.name}</h3><p>{report.report.report.skill_proposal.mechanism}</p><Button disabled={identity.role === 'member' || historical} title={historical ? '历史回放期间，固化操作保持禁用。' : undefined} onClick={() => setConfirmSkill(true)}>确认固化为技能</Button></>}</> : <p>{report?.job ? `审查状态：${report.job.status} · ${report.job.reason ?? '尚未生成报告'}` : '没有已登记的审查报告。'}</p>}</section>
        <section aria-label="任务产物来源"><h2>任务产物来源</h2>{artifactEvents.length ? artifactEvents.map((item) => <pre className="run-call-output" key={item.global_seq}>{JSON.stringify({ type: item.type, seq: item.seq, global_seq: item.global_seq, payload: item.payload }, null, 2)}</pre>) : <p>当前已读取范围没有产物事件。</p>}</section>
        <section className="run-event-list" aria-label="原始事件"><h2>原始事件</h2>{visibleEvents.map((event) => <article key={event.global_seq} data-event-seq={event.seq} className="run-event"><header><strong>{event.type}</strong><span>seq {event.seq} · global {event.global_seq} · 尝试 {event.attempt_no ?? '未登记'}</span></header><time>{new Date(event.ts).toLocaleString('zh-CN')}</time><details><summary>事件参数与真实结果</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>{['llm.request_started', 'llm.request_done', 'tool.prepared', 'tool.completed', 'tool.failed'].includes(event.type) && <Button onClick={() => void inspect(event)}>查看调用明细</Button>}{event.type === 'permission.requested' && <Button disabled>历史审批，仅供查看</Button>}</article>)}{!visibleEvents.length && <p>从头回放或单步查看已保存事件。</p>}</section>
      </> : <p>{taskId ? '正在读取任务事件。' : '选择一项真实执行记录。'}</p>}</section>
    </div>
    <Modal title="真实模型与工具调用" open={call !== null} footer={null} onCancel={() => setCall(null)}><p>{call?.kind} · {call?.id} · {call?.status}</p><pre className="run-call-output">{JSON.stringify(call, null, 2)}</pre></Modal>
    <Modal title="确认发布实际技能建议" open={confirmSkill} okText="确认固化" cancelText="取消固化" confirmLoading={busy} onCancel={() => setConfirmSkill(false)} onOk={() => void promote()}><p>报告：{report?.report?.id}</p><p>{report?.report?.report.skill_proposal?.outline}</p><p>正文由真实辅助模型提炼并发布新版本，后续Grant由管理员单独授予。</p></Modal>
  </main>
}
