import { useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { Button } from 'antd'
import { api, ApiFailure, setIdentityToken, useSession, useMemoryEvents, type Conversation } from './session'
import { Chat, Details, VerificationCards, type PendingVerification } from './Conversation'
import { Memory, MemoryNotice, memoryNames, type MemoryKind } from './Memory'
import { Governance } from './Governance'
import { AgentStudio } from './agents/AgentStudio'
import { AdminCenter } from './admin/AdminCenter'
import { RunCenter } from './run-center/RunCenter'
import { Automation } from './automation/Automation'
import { allPages, useGovernanceEvents, type Identity, type Resource, type Diagnostic } from './governance-data'

type NarrowPanel = 'tasks' | 'details' | null
type PageName = 'tasks' | 'governance' | 'admin' | 'runs' | 'studio' | 'automation' | MemoryKind

export default function App(): JSX.Element {
  const [generation, setGeneration] = useState(0)
  useEffect(() => {
    const changed = (): void => setGeneration((value) => value + 1)
    window.addEventListener('agentcrew-identity-changed', changed)
    return () => window.removeEventListener('agentcrew-identity-changed', changed)
  }, [])
  return <Application key={generation} />
}

function Application(): JSX.Element {
  const [selected, setSelected] = useState<string | null>(() => sessionStorage.getItem('conversation'))
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [draft, setDraft] = useState('')
  const [files, setFiles] = useState<string[]>([])
  const [folders, setFolders] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const [revision, setRevision] = useState(0)
  const [page, setPage] = useState<PageName>(() => (sessionStorage.getItem('memory-page') as PageName | null) ?? 'tasks')
  const [runTarget, setRunTarget] = useState<string | null>(null)
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [diagnostic, setDiagnostic] = useState<Diagnostic>({ mode: 'normal' })
  const [workspaces, setWorkspaces] = useState<Resource[]>([])
  const [agents, setAgents] = useState<Resource[]>([])
  const [workspaceId, setWorkspaceId] = useState(() => sessionStorage.getItem('workspace-id') ?? 'default')
  const [agentId, setAgentId] = useState(() => sessionStorage.getItem('agent-id') ?? 'default')
  const [governanceRevision, setGovernanceRevision] = useState(0)
  const [pendingVerifications, setPendingVerifications] = useState<PendingVerification[]>([])
  const governanceEvents = useGovernanceEvents(Boolean(identity) && identity?.organization_status === 'active' && diagnostic.mode === 'normal')
  const submission = useRef<{ content: string; id: string } | null>(null)
  const session = useSession(selected, revision)
  const conversation = selected ? conversations.find((item) => item.id === selected) : null
  const currentAgent = selected ? conversation?.agent_id ?? conversation?.agent_name ?? null : agentId
  const currentWorkspace = selected ? conversation?.workspace_id ?? workspaceId : workspaceId
  const state = session.snapshot
  const lastRun = session.events.filter((event) => event.type.startsWith("run.")).at(-1)
  const runRecordId = state?.current_task_run_id ?? lastRun?.task_run_id ?? null
  const displayState = state?.state === "error" ? "failed" : state?.state === "idle" && ["run.failed", "run.interrupted"].includes(lastRun?.type ?? "") ? lastRun!.type.slice(4) : state?.state
  const choose = (id: string | null): void => {
    setPage('tasks'); sessionStorage.removeItem('memory-page')
    setSelected(id); setActionError(''); setDraft(''); setFiles([]); setFolders([])
    submission.current = null
    if (id) sessionStorage.setItem('conversation', id)
    else sessionStorage.removeItem('conversation')
  }
  const action = async (path: string, body: unknown): Promise<void> => {
    setBusy(true); setActionError('')
    try { await api(path, body) }
    catch (error) { setActionError(error instanceof Error ? error.message : String(error)); setRevision((value) => value + 1) }
    finally { setBusy(false) }
  }
  const send = async (): Promise<void> => {
    if (!draft.trim() || busy) return
    setBusy(true); setActionError('')
    const content = JSON.stringify({ selected, draft, files, folders })
    if (submission.current?.content !== content) submission.current = { content, id: crypto.randomUUID() }
    const client_request_id = submission.current.id
    try {
      if (selected) await api(`/conversations/${selected}/instructions`, { text: draft, client_request_id })
      else {
        const created = await api<{ conversation: Conversation }>('/conversations', { instruction: draft, workspace_id: workspaceId, agent_id: agentId, import_files: files, folders, client_request_id })
        choose(created.conversation.id)
      }
      setDraft('')
      submission.current = null
      setConversations(await api<Conversation[]>('/conversations'))
    } catch (error) { setActionError(error instanceof Error ? error.message : String(error)) }
    finally { setBusy(false) }
  }
  const selectMaterials = async (kind: 'files' | 'folders'): Promise<void> => {
    const paths = await window.agentcrew.selectMaterials(kind)
    if (kind === 'files') setFiles((values) => [...new Set([...values, ...paths])])
    else setFolders((values) => [...new Set([...values, ...paths])])
  }
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [taskListOpen, setTaskListOpen] = useState(true)
  const [detailsOpen, setDetailsOpen] = useState(true)
  const [narrow, setNarrow] = useState(() => window.innerWidth < 1100)
  const [narrowPanel, setNarrowPanel] = useState<NarrowPanel>(null)
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'reconnecting'>('connecting')
  const memory = useMemoryEvents(currentWorkspace, currentAgent ?? '', connection === 'connected' && Boolean(currentAgent) && Boolean(currentWorkspace) && diagnostic.mode === 'normal' && identity?.organization_status === 'active')
  const studioMemory = useMemoryEvents(workspaceId, agentId, connection === 'connected' && page === 'studio' && Boolean(agentId) && Boolean(workspaceId) && diagnostic.mode === 'normal' && identity?.organization_status === 'active')
  const showMemory = (kind: MemoryKind): void => { setPage(kind); sessionStorage.setItem('memory-page', kind); setNarrowPanel(null) }
  const showPage = (target: PageName): void => { if (target !== 'runs') setRunTarget(null); setPage(target); sessionStorage.setItem('memory-page', target); setNarrowPanel(null) }
  const openRun = (taskId: string): void => { setRunTarget(taskId); setPage('runs'); sessionStorage.setItem('memory-page', 'runs'); setNarrowPanel(null) }
  const selectScope = (space: string, employee: string): void => { setWorkspaceId(space); setAgentId(employee); sessionStorage.setItem('workspace-id', space); sessionStorage.setItem('agent-id', employee) }
  const bumpGovernance = (): void => setGovernanceRevision((value) => value + 1)
  const sidebarButton = useRef<HTMLButtonElement>(null)
  const sidebarCloseButton = useRef<HTMLButtonElement>(null)
  const taskButton = useRef<HTMLButtonElement>(null)
  const taskCloseButton = useRef<HTMLButtonElement>(null)
  const detailsButton = useRef<HTMLButtonElement>(null)
  const detailsCloseButton = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    setPendingVerifications([])
    if (!selected || diagnostic.mode === 'diagnostic') return
    const controller = new AbortController()
    void api<PendingVerification[]>(`/conversations/${selected}/pending-verifications`, undefined, controller.signal)
      .then((calls) => { if (!controller.signal.aborted) setPendingVerifications(calls) })
      .catch((reason: Error) => { if (!controller.signal.aborted) setActionError(reason.message) })
    return () => controller.abort()
  }, [selected, session.snapshot?.at_global_seq, revision, diagnostic.mode])

  useEffect(() => {
    if (connection !== 'connected' || !identity || identity.organization_status !== 'active' || diagnostic.mode !== 'normal') return
    let active = true
    void api<Conversation[]>('/conversations').then((values) => {
      if (!active) return
      setConversations(values)
      if (selected && !values.some((item) => item.id === selected)) { setSelected(null); sessionStorage.removeItem('conversation'); setDraft(''); setFiles([]); setFolders([]) }
    }).catch((error: Error) => { if (active) setActionError(error.message) })
    return () => { active = false }
  }, [connection, session.snapshot?.state, identity?.effective_user_id, diagnostic.mode, governanceRevision, governanceEvents.revision])

  useEffect(() => {
    if (connection !== 'connected') return
    const controller = new AbortController()
    const load = async (): Promise<void> => {
      const [actualIdentity, actualDiagnostic] = await Promise.all([api<Identity>('/identity', undefined, controller.signal), api<Diagnostic>('/diagnostics', undefined, controller.signal)])
      if (controller.signal.aborted) return
      if (identity && identity.role !== actualIdentity.role) {
        const token = sessionStorage.getItem('agentcrew-identity') ?? ''
        setIdentityToken(token); sessionStorage.setItem('memory-page', page)
        return
      }
      setDiagnostic(actualDiagnostic)
      if (actualDiagnostic.mode === 'diagnostic') { setIdentity(actualIdentity); setPage('governance'); setSelected(null); setConversations([]); return }
      if (actualIdentity.organization_status !== 'active') { setIdentity(actualIdentity); setPage('governance'); setSelected(null); setConversations([]); setWorkspaces([]); setAgents([]); return }
      const [spaces, employees] = await Promise.all([allPages<Resource>('/workspaces', controller.signal), allPages<Resource>('/agents', controller.signal)])
      if (controller.signal.aborted) return
      setWorkspaces(spaces); setAgents(employees)
      const activeSpaces = spaces.filter((item) => item.status === 'active')
      const actualWorkspace = activeSpaces.some((item) => item.id === workspaceId) ? workspaceId : activeSpaces[0]?.id ?? ''
      const activeAgents = employees.filter((item) => item.status === 'active' && item.workspace_id === actualWorkspace)
      const actualAgent = activeAgents.some((item) => item.id === agentId) ? agentId : activeAgents[0]?.id ?? ''
      setWorkspaceId(actualWorkspace); setAgentId(actualAgent)
      sessionStorage.setItem('workspace-id', actualWorkspace); sessionStorage.setItem('agent-id', actualAgent)
      setIdentity(actualIdentity)
    }
    void load().catch((reason: Error) => {
      if (controller.signal.aborted) return
      setActionError(reason.message)
      if (reason instanceof ApiFailure && [401, 403].includes(reason.status)) {
        setIdentity(null); setSelected(null); setPage('governance'); setConversations([]); setWorkspaces([]); setAgents([]); setDraft(''); setFiles([]); setFolders([]); setPendingVerifications([])
      }
    })
    return () => controller.abort()
  }, [connection, governanceEvents.revision, governanceRevision])

  const changeDemo = async (user: string): Promise<void> => {
    setBusy(true); setActionError('')
    try {
      if (user === 'owner') { setIdentityToken(''); sessionStorage.setItem('memory-page', page); return }
      const issued = await api<{ identity_token: string }>('/identity/demo', { user_id: user, change_id: crypto.randomUUID() })
      setIdentityToken(issued.identity_token)
      sessionStorage.setItem('memory-page', page)
    } catch (reason) { setActionError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  function focusAfterRender(button: RefObject<HTMLButtonElement>): void {
    requestAnimationFrame(() => button.current?.focus())
  }

  useEffect(() => {
    const updateWidth = (): void => setNarrow(window.innerWidth < 1100)
    window.addEventListener('resize', updateWidth)
    return () => window.removeEventListener('resize', updateWidth)
  }, [])

  useEffect(() => {
    let active = true
    let connectedOnce = false
    let timer: ReturnType<typeof setTimeout>
    const check = async (): Promise<void> => {
      try {
        const [port, token] = await Promise.all([window.agentcrew.getBackendPort(), window.agentcrew.getToken()])
        if (!port || !token) throw new Error('任务服务尚未就绪')
        const response = await fetch(`http://127.0.0.1:${port}/api/health`, {
          headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(1500)
        })
        if (!response.ok || (await response.json() as { data?: { status?: string } }).data?.status !== 'ok') throw new Error('健康检查失败')
        if (active) { connectedOnce = true; setConnection('connected') }
      } catch {
        if (active) setConnection(connectedOnce ? 'reconnecting' : 'connecting')
      }
      if (active) timer = setTimeout(() => void check(), 1000)
    }
    void check()
    return () => { active = false; clearTimeout(timer) }
  }, [])

  useEffect(() => {
    if (!narrow || !narrowPanel) return
    const closeOnEscape = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return
      const button = narrowPanel === 'tasks' ? taskButton : detailsButton
      setNarrowPanel(null)
      focusAfterRender(button)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [narrow, narrowPanel])

  const showTasks = sidebarOpen && (narrow ? narrowPanel === 'tasks' : taskListOpen)
  const showDetails = narrow ? narrowPanel === 'details' : detailsOpen

  function toggleTasks(): void {
    if (narrow) {
      setSidebarOpen(true)
      setNarrowPanel(narrowPanel === 'tasks' ? null : 'tasks')
      if (narrowPanel === 'tasks') focusAfterRender(taskButton)
      return
    }
    if (!sidebarOpen) {
      setSidebarOpen(true)
      setTaskListOpen(true)
      focusAfterRender(taskCloseButton)
    } else {
      setTaskListOpen(!taskListOpen)
      focusAfterRender(taskListOpen ? taskButton : taskCloseButton)
    }
  }

  function toggleDetails(): void {
    if (narrow) {
      setNarrowPanel(narrowPanel === 'details' ? null : 'details')
      if (narrowPanel === 'details') focusAfterRender(detailsButton)
    } else {
      setDetailsOpen(!detailsOpen)
      focusAfterRender(detailsOpen ? detailsButton : detailsCloseButton)
    }
  }

  function toggleSidebar(): void {
    setSidebarOpen(!sidebarOpen)
    setNarrowPanel(null)
    focusAfterRender(sidebarOpen ? sidebarButton : sidebarCloseButton)
  }

  return (
    <div className="app-shell">
      <header className="titlebar">
        <span className="traffic-space" aria-hidden="true" />
        <span className="app-name">AgentCrew</span>
        <div className="title-actions">
          {!sidebarOpen && (
            <Button ref={sidebarButton} size="small" onClick={toggleSidebar} aria-label="展开侧栏">
              展开侧栏
            </Button>
          )}
          {(narrow || !showTasks) && (
            <Button
              ref={taskButton}
              size="small"
              onClick={toggleTasks}
              aria-label={showTasks ? '收起任务列表' : '展开任务列表'}
              aria-expanded={showTasks}
              aria-controls="task-list"
            >
              任务列表
            </Button>
          )}
          {(narrow || !showDetails) && (
            <Button
              ref={detailsButton}
              size="small"
              onClick={toggleDetails}
              aria-label={showDetails ? '收起运行详情' : '展开运行详情'}
              aria-expanded={showDetails}
              aria-controls="run-details"
            >
              运行详情
            </Button>
          )}
          <label className="identity-control"><span>演示身份</span><select aria-label="演示身份" value={identity?.effective_user_id ?? ''} disabled={busy || diagnostic.mode === 'diagnostic'} onChange={(event) => void changeDemo(event.target.value)}><option value="" disabled>等待服务端认证</option><option value="owner">真实owner</option><option value="wangming">王明 · 演示成员</option><option value="lilei">李蕾 · 演示成员</option></select></label>
          <Button type="primary" disabled={busy || !identity || identity.organization_status !== 'active' || diagnostic.mode === 'diagnostic'} onClick={() => choose(null)}>＋ 新建任务</Button>
        </div>
      </header>

      <div className="workspace">
        {sidebarOpen && (
          <nav className="navigation" aria-label="页面导航">
            <span className="nav-section">工作空间</span>
            <Button disabled={diagnostic.mode === 'diagnostic'} className={page === 'tasks' ? 'nav-current' : 'nav-link'} aria-current={page === 'tasks' ? 'page' : undefined} onClick={() => { setPage('tasks'); sessionStorage.removeItem('memory-page') }}>工作台</Button>
            {(Object.keys(memoryNames) as MemoryKind[]).map((kind) => <Button key={kind} disabled={diagnostic.mode === 'diagnostic'} className={page === kind ? 'nav-current' : 'nav-link'} aria-current={page === kind ? 'page' : undefined} onClick={() => showMemory(kind)}>{memoryNames[kind]}</Button>)}
            <span className="nav-section">管理</span>
            <Button disabled={diagnostic.mode === 'diagnostic'} className={page === 'studio' ? 'nav-current' : 'nav-link'} aria-current={page === 'studio' ? 'page' : undefined} onClick={() => showPage('studio')}>数字员工管理</Button>
            <Button className={page === 'admin' ? 'nav-current' : 'nav-link'} aria-current={page === 'admin' ? 'page' : undefined} onClick={() => showPage('admin')}>管理中心</Button>
            <Button className={page === 'governance' ? 'nav-current' : 'nav-link'} aria-current={page === 'governance' ? 'page' : undefined} onClick={() => showPage('governance')}>治理管理</Button>
            <Button disabled={diagnostic.mode === 'diagnostic'} className={page === 'runs' ? 'nav-current' : 'nav-link'} aria-current={page === 'runs' ? 'page' : undefined} onClick={() => showPage('runs')}>运行中心</Button>
            <Button disabled={diagnostic.mode === 'diagnostic'} className={page === 'automation' ? 'nav-current' : 'nav-link'} aria-current={page === 'automation' ? 'page' : undefined} onClick={() => showPage('automation')}>自动化</Button>
            <div className="nav-bottom">
              <Button ref={sidebarCloseButton} type="text" onClick={toggleSidebar} aria-label="收起侧栏">收起侧栏</Button>
            </div>
          </nav>
        )}

        {showTasks && page === 'tasks' && (
          <aside className="task-list" id="task-list" aria-label="任务列表">
            <div className="panel-heading">
              <h2>最近任务</h2>
              <Button ref={taskCloseButton} type="text" size="small" onClick={toggleTasks} aria-label="收起任务列表">收起</Button>
            </div>
            <div className="recent-tasks">{conversations.map((conversation) => <button key={conversation.id} disabled={busy} aria-current={selected === conversation.id ? 'page' : undefined} onClick={() => choose(conversation.id)}>{conversation.title || `任务 ${new Date(conversation.last_activity_at).toLocaleString('zh-CN')}`}<small>{conversation.state_badge}</small></button>)}</div>
          </aside>
        )}

        {page === 'automation' ? identity ? <Automation key={`${workspaceId}:${agentId}:${identity.effective_user_id}:${identity.role}`} identity={identity} workspace={workspaceId} agent={agentId} revision={governanceEvents.revision + governanceRevision} diagnostic={diagnostic} selectScope={selectScope} onRun={openRun} onConversation={choose} /> : <main className="automation-page"><p role="status">正在核查自动化读取权限。</p></main> : page === 'runs' ? identity ? <RunCenter identity={identity} revision={governanceEvents.revision + governanceRevision} initialTask={runTarget} /> : <main className="run-center-page"><p role="status">正在核查运行记录读取权限。</p></main> : page === 'governance' || page === 'studio' || page === 'admin' ? identity ? (page === 'studio' ? <AgentStudio identity={identity} workspace={workspaceId} agent={agentId} revision={governanceEvents.revision + governanceRevision} diagnostic={diagnostic} changed={bumpGovernance} onDiagnostic={setDiagnostic} selectScope={selectScope} events={studioMemory.events} connectionStatus={studioMemory.status} onRun={openRun} /> : page === 'admin' ? <AdminCenter identity={identity} workspace={workspaceId} agent={agentId} revision={governanceEvents.revision + governanceRevision} diagnostic={diagnostic} changed={bumpGovernance} onDiagnostic={setDiagnostic} selectScope={selectScope} onRun={openRun} /> : <Governance identity={identity} workspace={workspaceId} agent={agentId} revision={governanceEvents.revision + governanceRevision} diagnostic={diagnostic} changed={bumpGovernance} onDiagnostic={setDiagnostic} selectScope={selectScope} />) : <main className="governance-page"><p role="status">正在读取当前治理身份。</p>{actionError && <p role="alert">{actionError}</p>}<Button onClick={() => setIdentityToken('')}>使用真实所有者凭证重新认证</Button></main> : page !== 'tasks' ? !identity || currentAgent === null ? <main className="memory-page" aria-busy="true"><p role="status">正在读取当前员工与工作区。</p></main> : <Memory key={`${page}:${currentAgent}`} kind={page} workspace={currentWorkspace} agent={currentAgent} events={memory.events} connectionStatus={memory.status} identity={identity} /> : <main className="task-content">
          <div className="content-scroll">
            <div className="task-heading">
              <h1>{selected ? conversations.find((item) => item.id === selected)?.title || '任务对话' : '给数字员工交代一项工作'}</h1>
              <span className="connection-label" role="status">{connection === 'connected' ? '任务服务已连接' : connection === 'reconnecting' ? '任务服务正在重新连接' : '正在连接任务服务'}</span>
            </div>
            <div className="notice" role="status">{selected ? `${session.status} · ${displayState ?? '正在加载'} · 等待审批 ${state?.waiting_approvals ?? 0} · 等待回答 ${state?.waiting_questions ?? 0}` : '输入任务指令，可以附加文件或授权文件夹。'}
              {displayState === 'interrupted' && lastRun?.task_run_id && <Button disabled={busy || pendingVerifications.length > 0 || connection !== 'connected' || session.status !== '已连接'} loading={busy} onClick={() => void action(`/task-runs/${lastRun.task_run_id}/resume`, {})}>恢复</Button>}
            </div>
            <MemoryNotice events={memory.events} />
            {runRecordId && <Button onClick={() => openRun(runRecordId)}>进入此任务运行记录</Button>}
            <VerificationCards calls={pendingVerifications} refreshed={() => setRevision((value) => value + 1)} />
            {(actionError || session.error) && <p id="request-error" role="alert">{actionError || session.error}</p>}
            {selected ? <Chat events={session.events} replayedThrough={session.replayedThrough} /> : <div className="empty-workspace">
              <div className="empty-symbol" aria-hidden="true">＋</div>
              <h2>创建一项任务</h2>
              <p>任务执行过程、审批和提问会显示在运行详情中。</p>
            </div>}
          </div>
          <div className="composer-area">
            {!selected && <div className="task-selection"><label><span>工作区</span><select aria-label="工作区" value={workspaceId} disabled={busy} onChange={(event) => { const selectedWorkspace = event.target.value; setWorkspaceId(selectedWorkspace); sessionStorage.setItem('workspace-id', selectedWorkspace); const selectedAgent = agents.find((item) => item.workspace_id === selectedWorkspace && item.status === 'active')?.id ?? ''; setAgentId(selectedAgent); sessionStorage.setItem('agent-id', selectedAgent) }}>{workspaces.filter((item) => item.status === 'active').map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label><span>数字员工</span><select aria-label="数字员工" value={agentId} disabled={busy} onChange={(event) => { setAgentId(event.target.value); sessionStorage.setItem('agent-id', event.target.value) }}>{agents.filter((item) => item.workspace_id === workspaceId && item.status === 'active').map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><small>{identity?.demo ? '演示身份' : '真实身份'}：{identity?.name ?? '正在验证'} · {identity?.role}</small></div>}
            <div className="scope-line">
              {selected && <span>数字员工：{conversations.find((item) => item.id === selected)?.agent_name}</span>}
              {session.scope && <details><summary>文件访问范围</summary><p>工作空间：{session.scope.workspace_dir}</p><p>任务材料：{session.scope.materials_dir}</p>{session.scope.folders.map((folder) => <p key={folder.path}>{folder.path}（{folder.access}）</p>)}</details>}
              {!selected && [...files, ...folders].map((path) => <span key={path}>{path}<Button size="small" aria-label={`移除 ${path}`} onClick={() => { setFiles(files.filter((value) => value !== path)); setFolders(folders.filter((value) => value !== path)) }}>移除</Button></span>)}
            </div>
            {state && state.queue.length > 0 && <section className="queue" aria-label="排队指令"><p>{state.queue_paused ? '队列已暂停' : '排队中'} · {state.queue.length} 条</p>{state.queue.map((item) => <p key={item.id}>{item.text}<Button disabled={busy} size="small" onClick={() => void action(`/conversations/${selected}/queue/cancel`, { item_ids: [item.id] })}>取消指令</Button></p>)}<Button disabled={busy || !state.can_continue_queue} onClick={() => void action(`/conversations/${selected}/queue/continue`, {})}>继续队列</Button><Button disabled={busy} onClick={() => void action(`/conversations/${selected}/queue/cancel`, { all: true })}>取消剩余</Button></section>}
            <div className="composer">
              <textarea aria-label="任务指令" aria-describedby="composer-help" value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  if (connection === "connected" && identity && diagnostic.mode === 'normal' && agentId && (!selected || state?.can_send || state?.can_queue)) void send()
                }
              }} disabled={busy} placeholder="输入任务指令" />
              <small id="composer-help">{state?.can_queue ? '新指令将进入队列。' : selected && !state?.can_send ? '当前状态暂时无法发送指令。' : 'Enter 发送，Shift+Enter 换行。'}</small>
              <div className="composer-actions">
                <div>
                  <Button disabled={Boolean(selected) || busy} onClick={() => void selectMaterials('files')}>添加文件</Button>
                  <Button disabled={Boolean(selected) || busy} onClick={() => void selectMaterials('folders')}>选择文件夹</Button>
                </div>
                <div>{state?.can_cancel && <Button danger disabled={busy} onClick={() => void action(`/task-runs/${state.current_task_run_id}/cancel`, {})}>停止任务</Button>}<Button type="primary" disabled={busy || !identity || diagnostic.mode !== 'normal' || !agentId || !draft.trim() || connection !== 'connected' || Boolean(selected && !state?.can_send && !state?.can_queue)} onClick={() => void send()}>{state?.can_queue ? '加入队列' : '发送任务'}</Button></div>
              </div>
            </div>
          </div>
        </main>}

        {showDetails && page === 'tasks' && (
          <aside className="run-details" id="run-details" aria-label="运行详情">
            <div className="panel-heading">
              <h2>运行详情</h2>
              <Button ref={detailsCloseButton} type="text" size="small" onClick={toggleDetails} aria-label="收起运行详情">收起</Button>
            </div>
            <Details events={session.events} action={(path, body) => void action(path, body)} busy={busy} canManage={identity?.role !== 'member' && diagnostic.mode === 'normal'} />
          </aside>
        )}
      </div>
    </div>
  )
}
