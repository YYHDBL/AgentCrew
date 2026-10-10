import { useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { Badge, Button, Dropdown, Input, Tabs, Tooltip } from 'antd'
import { AppstoreOutlined, ArrowDownOutlined, ArrowUpOutlined, BellOutlined, BookOutlined, ClockCircleOutlined, CloseOutlined, FileTextOutlined, FolderOpenOutlined, MenuFoldOutlined, MenuUnfoldOutlined, PaperClipOutlined, PlusOutlined, ProfileOutlined, SafetyCertificateOutlined, SearchOutlined, SettingOutlined, TeamOutlined, UnorderedListOutlined, UserOutlined } from '@ant-design/icons'
import { api, ApiFailure, setIdentityToken, useSession, useMemoryEvents, type Conversation } from './session'
import { Artifacts, Chat, Details, ExecutionSummary, VerificationCards, type PendingVerification } from './Conversation'
import { currentState, statusNames, taskTitle, displayTime } from './conversation-view'
import { Memory, MemoryNotice, memoryNames, type MemoryKind } from './Memory'
import { Governance } from './Governance'
import { AgentStudio } from './agents/AgentStudio'
import { AdminCenter } from './admin/AdminCenter'
import { RunCenter } from './run-center/RunCenter'
import { Automation } from './automation/Automation'
import { DesktopSettings } from './DesktopSettings'
import { NotificationCenter, useNotifications } from './Notifications'
import { ExitConfirmation } from './ExitConfirmation'
import type { Notification } from './api/types'
import type { components } from './api/generated/http'
import { allPages, useGovernanceEvents, type Identity, type Resource, type Diagnostic } from './governance-data'
import appIcon from '../../../assets/brand/agentcrew-icon.png'

type NarrowPanel = 'tasks' | 'details' | null
type PageName = 'tasks' | 'governance' | 'admin' | 'runs' | 'studio' | 'automation' | 'settings' | 'notifications' | MemoryKind

export default function App(): JSX.Element {
  const [generation, setGeneration] = useState(0)
  const [exitPending, setExitPending] = useState(false)
  useEffect(() => {
    const changed = (): void => setGeneration((value) => value + 1)
    window.addEventListener('agentcrew-identity-changed', changed)
    return () => window.removeEventListener('agentcrew-identity-changed', changed)
  }, [])
  return <><Application key={generation} exitPending={exitPending} /><ExitConfirmation onPendingChange={setExitPending} /></>
}

function Application({ exitPending }: { exitPending: boolean }): JSX.Element {
  const [selected, setSelected] = useState<string | null>(() => sessionStorage.getItem('conversation'))
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [taskSearch, setTaskSearch] = useState('')
  const [taskMetadata, setTaskMetadata] = useState<Record<string, { instruction?: string; status?: string }>>({})
  const [artifacts, setArtifacts] = useState<components['schemas']['Artifact'][]>([])
  const [draft, setDraft] = useState('')
  const [files, setFiles] = useState<string[]>([])
  const [folders, setFolders] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const [revision, setRevision] = useState(0)
  const [page, setPage] = useState<PageName>(() => (sessionStorage.getItem('memory-page') as PageName | null) ?? 'tasks')
  const [runTarget, setRunTarget] = useState<string | null>(null)
  const [jobTarget, setJobTarget] = useState<string | null>(null)
  const [notificationTarget, setNotificationTarget] = useState<string | null>(null)
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
  const displayState = currentState(session.events, state?.state, state?.waiting_approvals, state?.waiting_questions)
  const firstInstruction = session.events.find((event) => event.type === 'run.queued')?.payload.instruction
  const selectedTitle = taskTitle(conversation?.title, typeof firstInstruction === 'string' ? firstInstruction : taskMetadata[selected ?? '']?.instruction)
  const activeAgentName = agents.find((item) => item.id === currentAgent)?.name ?? conversation?.agent_name ?? '数字员工'
  const activeWorkspaceName = workspaces.find((item) => item.id === currentWorkspace)?.name ?? '正在读取工作空间'
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
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth)
  const narrow = viewportWidth < 1100
  const compact = viewportWidth < 980
  const [narrowPanel, setNarrowPanel] = useState<NarrowPanel>(null)
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'reconnecting'>('connecting')
  const desktopEnabled = connection === 'connected' && identity?.organization_status === 'active' && diagnostic.mode === 'normal'
  const readsEnabled = desktopEnabled && !exitPending
  const notifications = useNotifications(desktopEnabled, `${identity?.effective_user_id ?? ''}:${identity?.role ?? ''}`, exitPending)
  const memory = useMemoryEvents(currentWorkspace, currentAgent ?? '', connection === 'connected' && Boolean(currentAgent) && Boolean(currentWorkspace) && diagnostic.mode === 'normal' && identity?.organization_status === 'active')
  const studioMemory = useMemoryEvents(workspaceId, agentId, connection === 'connected' && page === 'studio' && Boolean(agentId) && Boolean(workspaceId) && diagnostic.mode === 'normal' && identity?.organization_status === 'active')
  const showMemory = (kind: MemoryKind): void => { setPage(kind); sessionStorage.setItem('memory-page', kind); setNarrowPanel(null) }
  const showPage = (target: PageName): void => { if (target !== 'runs') setRunTarget(null); if (target !== 'automation') setJobTarget(null); setPage(target); sessionStorage.setItem('memory-page', target); setNarrowPanel(null) }
  const openRun = (taskId: string): void => { setRunTarget(taskId); setPage('runs'); sessionStorage.setItem('memory-page', 'runs'); setNarrowPanel(null) }
  const selectScope = (space: string, employee: string): void => { setWorkspaceId(space); setAgentId(employee); sessionStorage.setItem('workspace-id', space); sessionStorage.setItem('agent-id', employee) }
  const openJob = (notification: Notification): void => {
    selectScope(notification.workspace_id, notification.agent_id)
    setJobTarget(notification.job_id)
    showPage('automation')
  }
  const bumpGovernance = (): void => setGovernanceRevision((value) => value + 1)
  const sidebarButton = useRef<HTMLButtonElement>(null)
  const sidebarCloseButton = useRef<HTMLButtonElement>(null)
  const taskButton = useRef<HTMLButtonElement>(null)
  const taskCloseButton = useRef<HTMLButtonElement>(null)
  const detailsButton = useRef<HTMLButtonElement>(null)
  const detailsCloseButton = useRef<HTMLButtonElement>(null)
  const contentScroll = useRef<HTMLDivElement>(null)
  const followMessages = useRef(true)
  const [awayFromLatest, setAwayFromLatest] = useState(false)

  const toLatest = (): void => {
    const viewport = contentScroll.current
    if (!viewport) return
    viewport.scrollTo({ top: viewport.scrollHeight, behavior: 'instant' })
    followMessages.current = true; setAwayFromLatest(false)
  }

  useEffect(() => {
    followMessages.current = true; setAwayFromLatest(false)
  }, [selected])

  useEffect(() => {
    if (followMessages.current) toLatest()
  }, [session.events.length, page, selected])

  useEffect(() => {
    setArtifacts([])
  }, [selected, desktopEnabled])

  useEffect(() => {
    if (!selected || !readsEnabled) return
    const controller = new AbortController()
    void api<components['schemas']['Artifact'][]>(`/conversations/${selected}/artifacts`, undefined, controller.signal).then((items) => {
      if (!controller.signal.aborted) setArtifacts(items)
    }, (reason: Error) => {
      if (controller.signal.aborted) return
      setActionError(reason.message)
      throw reason
    })
    return () => controller.abort()
  }, [selected, readsEnabled, session.snapshot?.at_global_seq])

  useEffect(() => window.agentcrew.onNotificationClick((target) => setNotificationTarget(target.id)), [])

  useEffect(() => {
    if (!readsEnabled || !notificationTarget) return
    let active = true
    showPage('notifications')
    void notifications.read(notificationTarget).then((notification) => {
      if (!active) return
      setNotificationTarget(null)
      if (!notification) return
      if (notification.task_run_id) openRun(notification.task_run_id)
      else if (notification.job_id) openJob(notification)
    })
    return () => { active = false }
  }, [readsEnabled, notificationTarget, identity?.effective_user_id, identity?.role])

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
    if (!readsEnabled) return
    const controller = new AbortController()
    void Promise.all(conversations.filter((item) => !item.title).map(async (item) => {
      const runs = await api<components['schemas']['TaskRunSummary'][]>(`/conversations/${item.id}/task-runs`, undefined, controller.signal)
      return [item.id, { instruction: runs[0]?.instruction, status: runs.at(-1)?.status }] as const
    })).then((items) => {
      if (!controller.signal.aborted) setTaskMetadata(Object.fromEntries(items))
    }, (reason: Error) => {
      if (controller.signal.aborted) return
      setActionError(reason.message)
      throw reason
    })
    return () => controller.abort()
  }, [conversations, readsEnabled])

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
    const updateWidth = (): void => setViewportWidth(window.innerWidth)
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

  const showTasks = sidebarOpen && (compact ? narrowPanel === 'tasks' : taskListOpen)
  const showDetails = narrow ? narrowPanel === 'details' : detailsOpen
  const currentRunEvents = session.events.filter((event) => event.task_run_id === runRecordId || event.payload.source_task_run_id === runRecordId)
  const pageTitles: Record<PageName, string> = { ...memoryNames, tasks: selected ? selectedTitle : '新建任务', runs: '运行中心', automation: '自动化', notifications: '通知中心', settings: '设置', studio: '设置', admin: '设置', governance: '设置' }
  const pageTitle = pageTitles[page]
  const settingsPage = ['settings', 'studio', 'admin', 'governance'].includes(page)
  const settingsItems = [
    { key: 'settings', label: <><SettingOutlined /> 桌面行为</>, children: <DesktopSettings identity={identity} enabled={desktopEnabled} /> },
    { key: 'studio', label: <><TeamOutlined /> 数字员工</>, disabled: diagnostic.mode === 'diagnostic', children: identity && <AgentStudio identity={identity} workspace={workspaceId} agent={agentId} revision={governanceEvents.revision + governanceRevision} diagnostic={diagnostic} changed={bumpGovernance} onDiagnostic={setDiagnostic} selectScope={selectScope} events={studioMemory.events} connectionStatus={studioMemory.status} onRun={openRun} /> },
    { key: 'admin', label: <><UserOutlined /> 组织管理</>, children: identity && <AdminCenter identity={identity} workspace={workspaceId} agent={agentId} revision={governanceEvents.revision + governanceRevision} diagnostic={diagnostic} changed={bumpGovernance} onDiagnostic={setDiagnostic} selectScope={selectScope} onRun={openRun} /> },
    { key: 'governance', label: <><SafetyCertificateOutlined /> 权限与审计</>, children: identity && <Governance identity={identity} workspace={workspaceId} agent={agentId} revision={governanceEvents.revision + governanceRevision} diagnostic={diagnostic} changed={bumpGovernance} onDiagnostic={setDiagnostic} selectScope={selectScope} /> }
  ]

  function toggleTasks(): void {
    if (compact) {
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
      focusAfterRender(narrowPanel === 'details' ? detailsButton : detailsCloseButton)
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
        <span className="app-name" title={pageTitle}>{page === 'tasks' ? <FileTextOutlined /> : <AppstoreOutlined />}<span>{pageTitle}</span></span>
        <div className="title-actions">
          {!sidebarOpen && (
            <Tooltip title="展开侧栏"><Button ref={sidebarButton} type="text" icon={<MenuUnfoldOutlined />} onClick={toggleSidebar} aria-label="展开侧栏" /></Tooltip>
          )}
          {page === 'tasks' && (compact || !showTasks) && (
            <Tooltip title={showTasks ? '收起任务列表' : '展开任务列表'}>
            <Button
              ref={taskButton}
              type="text"
              icon={<UnorderedListOutlined />}
              onClick={toggleTasks}
              aria-label={showTasks ? '收起任务列表' : '展开任务列表'}
              aria-expanded={showTasks}
              aria-controls="task-list"
            />
            </Tooltip>
          )}
          {page === 'tasks' && (
            <Tooltip title={showDetails ? '收起运行详情' : '展开运行详情'}>
            <Button
              ref={detailsButton}
              type="text"
              icon={<ProfileOutlined />}
              onClick={toggleDetails}
              aria-label={showDetails ? '收起运行详情' : '展开运行详情'}
              aria-expanded={showDetails}
              aria-controls="run-details"
            />
            </Tooltip>
          )}
          <Tooltip title={`通知中心 · ${notifications.unread} 条未读`}><Badge count={notifications.unread} overflowCount={99}><Button type="text" icon={<BellOutlined />} aria-label={`通知中心，${notifications.unread} 条未读通知`} onClick={() => showPage('notifications')} /></Badge></Tooltip>
          {!sidebarOpen && <Button icon={<PlusOutlined />} type="primary" disabled={busy || !identity || identity.organization_status !== 'active' || diagnostic.mode === 'diagnostic'} onClick={() => choose(null)}>新建任务</Button>}
        </div>
      </header>

      <div className="workspace">
        {sidebarOpen && (
          <nav className={`navigation ${compact && showTasks ? 'navigation-expanded' : ''}`} aria-label="页面导航">
            <div className="nav-brand"><img src={appIcon} alt="" /><span>AgentCrew</span></div>
            <Tooltip title="新建任务" placement="right"><Button className="nav-link" icon={<PlusOutlined />} aria-label="新建任务" disabled={busy || !identity || identity.organization_status !== 'active' || diagnostic.mode === 'diagnostic'} onClick={() => choose(null)}><span className="nav-label">新建任务</span></Button></Tooltip>
            <Tooltip title="工作台" placement="right"><Button icon={<AppstoreOutlined />} aria-label="工作台" disabled={diagnostic.mode === 'diagnostic'} className={page === 'tasks' ? 'nav-current' : 'nav-link'} aria-current={page === 'tasks' ? 'page' : undefined} onClick={() => { setPage('tasks'); sessionStorage.removeItem('memory-page') }}><span className="nav-label">工作台</span></Button></Tooltip>
            <Dropdown trigger={['click']} menu={{ items: (Object.keys(memoryNames) as MemoryKind[]).map((kind) => ({ key: kind, label: memoryNames[kind] })), selectedKeys: [page], onClick: ({ key }) => showMemory(key as MemoryKind) }}><Button aria-label="记忆与技能" icon={<BookOutlined />} disabled={diagnostic.mode === 'diagnostic'} className={page in memoryNames ? 'nav-current' : 'nav-link'}><span className="nav-label">记忆与技能</span></Button></Dropdown>
            <Tooltip title="运行中心" placement="right"><Button icon={<ProfileOutlined />} aria-label="运行中心" disabled={diagnostic.mode === 'diagnostic'} className={page === 'runs' ? 'nav-current' : 'nav-link'} aria-current={page === 'runs' ? 'page' : undefined} onClick={() => showPage('runs')}><span className="nav-label">运行中心</span></Button></Tooltip>
            <Tooltip title="自动化" placement="right"><Button icon={<ClockCircleOutlined />} aria-label="自动化" disabled={diagnostic.mode === 'diagnostic'} className={page === 'automation' ? 'nav-current' : 'nav-link'} aria-current={page === 'automation' ? 'page' : undefined} onClick={() => showPage('automation')}><span className="nav-label">自动化</span></Button></Tooltip>
            {showTasks && <section className="task-list" id="task-list" aria-label="任务列表">
              <div className="panel-heading"><h2>最近任务</h2><Tooltip title="收起任务列表"><Button ref={taskCloseButton} type="text" size="small" icon={<CloseOutlined />} onClick={toggleTasks} aria-label="收起任务列表" /></Tooltip></div>
              <Input className="task-search" prefix={<SearchOutlined />} aria-label="搜索任务" placeholder="搜索任务" value={taskSearch} allowClear onChange={(event) => setTaskSearch(event.target.value)} />
              <div className="recent-tasks">{conversations.filter((item) => taskTitle(item.title, taskMetadata[item.id]?.instruction).toLocaleLowerCase().includes(taskSearch.toLocaleLowerCase())).map((item) => {
                const title = taskTitle(item.title, taskMetadata[item.id]?.instruction)
                const taskState = item.state_badge === 'idle' ? taskMetadata[item.id]?.status ?? 'idle' : item.state_badge
                return <button key={item.id} data-conversation-id={item.id} disabled={busy} aria-current={selected === item.id && page === 'tasks' ? 'page' : undefined} title={title} onClick={() => choose(item.id)}><span className="task-row-title">{title}</span><span className="task-row-meta"><span className={`status-dot status-${taskState}`} aria-hidden="true" />{statusNames[taskState] ?? taskState}<time dateTime={item.last_activity_at}>{displayTime(item.last_activity_at)}</time></span></button>
              })}</div>
              {conversations.length === 0 && <p className="task-list-empty">创建任务后，可在这里继续处理。</p>}
              {taskSearch && !conversations.some((item) => taskTitle(item.title, taskMetadata[item.id]?.instruction).toLocaleLowerCase().includes(taskSearch.toLocaleLowerCase())) && <p className="task-list-empty">没有符合条件的任务。</p>}
            </section>}
            <div className="nav-bottom">
              <Tooltip title="设置" placement="right"><Button icon={<SettingOutlined />} className={settingsPage ? 'nav-current' : 'nav-link'} aria-label="设置" aria-current={settingsPage ? 'page' : undefined} onClick={() => showPage('settings')}><span className="nav-label">设置</span></Button></Tooltip>
              <Tooltip title="收起侧栏" placement="right"><Button ref={sidebarCloseButton} type="text" icon={<MenuFoldOutlined />} onClick={toggleSidebar} aria-label="收起侧栏" /></Tooltip>
            </div>
          </nav>
        )}

        {settingsPage ? <div className="settings-shell">
          <div className="settings-heading"><h1>设置</h1><p>桌面行为、数字员工、组织管理与权限设置。</p>
            <label className="identity-control"><span>演示身份</span><select aria-label="演示身份" value={identity?.effective_user_id ?? ''} disabled={busy || diagnostic.mode === 'diagnostic'} onChange={(event) => void changeDemo(event.target.value)}><option value="" disabled>等待服务端认证</option><option value="owner">真实所有者</option><option value="wangming">王明 · 演示成员</option><option value="lilei">李蕾 · 演示成员</option></select></label>
            {actionError && <p role="alert">{actionError}</p>}
            {!identity && <><p role="status">正在读取当前治理身份。</p><Button onClick={() => setIdentityToken('')}>使用真实所有者凭证重新认证</Button></>}
          </div>
          <Tabs activeKey={page} onChange={(target) => showPage(target as PageName)} items={settingsItems} destroyOnHidden />
        </div> : page === 'notifications' ? <NotificationCenter notifications={notifications} enabled={desktopEnabled} onRun={openRun} onJob={openJob} /> : page === 'automation' ? identity ? <Automation key={`${workspaceId}:${agentId}:${identity.effective_user_id}:${identity.role}`} identity={identity} workspace={workspaceId} agent={agentId} revision={governanceEvents.revision + governanceRevision} diagnostic={diagnostic} selectScope={selectScope} onRun={openRun} onConversation={choose} initialJob={jobTarget} /> : <main className="automation-page"><p role="status">正在核查自动化读取权限。</p></main> : page === 'runs' ? identity ? <RunCenter identity={identity} revision={governanceEvents.revision + governanceRevision} initialTask={runTarget} /> : <main className="run-center-page"><p role="status">正在核查运行记录读取权限。</p></main> : page !== 'tasks' ? !identity || currentAgent === null ? <main className="memory-page" aria-busy="true"><p role="status">正在读取当前员工与工作区。</p></main> : <Memory key={`${page}:${currentAgent}`} kind={page as MemoryKind} workspace={currentWorkspace} agent={currentAgent} events={memory.events} connectionStatus={memory.status} identity={identity} /> : <main className="task-content">
          <div className="content-scroll" ref={contentScroll} onScroll={(event) => {
            const viewport = event.currentTarget
            const atLatest = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 80
            followMessages.current = atLatest; setAwayFromLatest(!atLatest)
          }}>
            <div className="conversation-column">
            {selected && <div className="task-heading"><div><h1>{selectedTitle}</h1><p>{activeWorkspaceName} · {activeAgentName}<span className={`task-status status-${displayState}`}>{statusNames[displayState]}</span></p></div>{runRecordId && <Tooltip title="查看运行记录与回放"><Button type="text" icon={<ProfileOutlined />} aria-label="查看运行记录" onClick={() => openRun(runRecordId)} /></Tooltip>}</div>}
            <div className={`connection-label connection-${connection}`} role="status">{connection === 'connected' ? '任务服务已连接' : connection === 'reconnecting' ? '任务服务正在重新连接' : '正在连接任务服务'}</div>
            {selected && ['interrupted', 'failed', 'waiting_approval', 'waiting_question', 'waiting_verification'].includes(displayState) && <div className="notice" role="status">{statusNames[displayState]}{pendingVerifications.length ? ` · ${pendingVerifications.length} 项操作需要核验` : ''}
              {displayState === 'interrupted' && lastRun?.task_run_id && <Button disabled={busy || pendingVerifications.length > 0 || connection !== 'connected' || session.status !== '已连接'} loading={busy} onClick={() => void action(`/task-runs/${lastRun.task_run_id}/resume`, {})}>恢复</Button>}
            </div>}
            <MemoryNotice events={memory.events} />
            <VerificationCards calls={pendingVerifications} refreshed={() => setRevision((value) => value + 1)} />
            {(actionError || session.error) && <p id="request-error" role="alert">{actionError || session.error}</p>}
            {selected ? <>
              <Chat events={session.events} agentName={activeAgentName} />
              <ExecutionSummary events={currentRunEvents} onDetails={() => { if (!showDetails) toggleDetails() }} />
              <Details mode="attention" events={session.events} action={(path, body) => void action(path, body)} busy={busy} canManage={identity?.role !== 'member' && diagnostic.mode === 'normal'} />
              <Artifacts items={artifacts} />
              {state?.state === 'running' && <p className="working-indicator" role="status"><span className="status-dot status-running" />{activeAgentName}正在处理任务</p>}
            </> : <div className="empty-workspace">
              <div className="empty-symbol" aria-hidden="true"><img src={appIcon} alt="" /></div>
              <span className="empty-eyebrow">你的数字员工工作空间</span>
              <h1>今天，需要处理什么？</h1>
              <p>交代任务、附上材料，执行过程和需要你决定的事项会显示在这里。</p>
              <div className="empty-capabilities"><span><FileTextOutlined />文件与资料</span><span><ProfileOutlined />整理与分析</span><span><ClockCircleOutlined />计划与日常工作</span></div>
            </div>}
            </div>
          </div>
          {awayFromLatest && <Tooltip title="返回最新内容"><Button className="jump-to-latest" shape="circle" icon={<ArrowDownOutlined />} aria-label="返回最新内容" onClick={toLatest} /></Tooltip>}
          <div className="composer-area">
            {state && state.queue.length > 0 && <section className="queue" aria-label="排队指令"><p>{state.queue_paused ? '队列已暂停' : '排队中'} · {state.queue.length} 条</p>{state.queue.map((item) => <p key={item.id}>{item.text}<Button disabled={busy} size="small" onClick={() => void action(`/conversations/${selected}/queue/cancel`, { item_ids: [item.id] })}>取消指令</Button></p>)}<Button disabled={busy || !state.can_continue_queue} onClick={() => void action(`/conversations/${selected}/queue/continue`, {})}>继续队列</Button><Button disabled={busy} onClick={() => void action(`/conversations/${selected}/queue/cancel`, { all: true })}>取消剩余</Button></section>}
            <div className="composer">
              {!selected && (files.length > 0 || folders.length > 0) && <div className="attachment-list">{[...files, ...folders].map((path) => <span className="attachment-chip" key={path} title={path}>{files.includes(path) ? <PaperClipOutlined /> : <FolderOpenOutlined />}<span>{path.split('/').at(-1)}</span><Button type="text" size="small" icon={<CloseOutlined />} aria-label={`移除 ${path}`} onClick={() => { setFiles(files.filter((value) => value !== path)); setFolders(folders.filter((value) => value !== path)) }} /></span>)}</div>}
              <Input.TextArea autoSize={{ minRows: 2, maxRows: 8 }} aria-label="任务指令" aria-describedby="composer-help" value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  if (connection === "connected" && identity && diagnostic.mode === 'normal' && agentId && (!selected || state?.can_send || state?.can_queue)) void send()
                }
              }} disabled={busy} placeholder={selected ? '继续交代任务，或者补充说明…' : '交代一项工作，或附上需要处理的资料…'} />
              <div className="composer-actions">
                <div className="composer-tools">
                  <Tooltip title="添加文件"><Button type="text" icon={<PaperClipOutlined />} aria-label="添加文件" disabled={Boolean(selected) || busy} onClick={() => void selectMaterials('files')} /></Tooltip>
                  <Tooltip title="选择文件夹"><Button type="text" icon={<FolderOpenOutlined />} aria-label="选择文件夹" disabled={Boolean(selected) || busy} onClick={() => void selectMaterials('folders')} /></Tooltip>
                  {!selected ? <div className="task-selection"><label><FolderOpenOutlined /><select aria-label="工作区" value={workspaceId} disabled={busy} onChange={(event) => { const selectedWorkspace = event.target.value; setWorkspaceId(selectedWorkspace); sessionStorage.setItem('workspace-id', selectedWorkspace); const selectedAgent = agents.find((item) => item.workspace_id === selectedWorkspace && item.status === 'active')?.id ?? ''; setAgentId(selectedAgent); sessionStorage.setItem('agent-id', selectedAgent) }}>{workspaces.filter((item) => item.status === 'active').map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label><TeamOutlined /><select aria-label="数字员工" value={agentId} disabled={busy} onChange={(event) => { setAgentId(event.target.value); sessionStorage.setItem('agent-id', event.target.value) }}>{agents.filter((item) => item.workspace_id === workspaceId && item.status === 'active').map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div> : <span className="composer-context"><FolderOpenOutlined />{activeWorkspaceName}<TeamOutlined />{activeAgentName}</span>}
                  {session.scope && <details className="scope-picker" onKeyDown={(event) => { if (event.key === 'Escape') { event.currentTarget.open = false; event.currentTarget.querySelector('summary')?.focus() } }}><summary aria-label="文件访问范围"><SafetyCertificateOutlined /><span>文件访问范围</span></summary><div><h3>文件访问范围</h3><p>工作空间：{session.scope.workspace_dir}</p><p>任务材料：{session.scope.materials_dir}</p>{session.scope.folders.map((folder) => <p key={folder.path}>{folder.path}（{folder.access}）</p>)}</div></details>}
                </div>
                <div>{state?.can_cancel && <Tooltip title="停止任务"><Button danger shape="circle" icon={<CloseOutlined />} aria-label="停止任务" disabled={busy} onClick={() => void action(`/task-runs/${state.current_task_run_id}/cancel`, {})} /></Tooltip>}<Tooltip title={state?.can_queue ? '加入队列' : '发送任务'}><Button type="primary" shape="circle" icon={<ArrowUpOutlined />} aria-label={state?.can_queue ? '加入队列' : '发送任务'} disabled={busy || !identity || diagnostic.mode !== 'normal' || !agentId || !draft.trim() || connection !== 'connected' || Boolean(selected && !state?.can_send && !state?.can_queue)} onClick={() => { toLatest(); void send() }} /></Tooltip></div>
              </div>
            </div>
            <div className="composer-caption"><small id="composer-help">{diagnostic.mode !== 'normal' ? '当前处于只读诊断，任务提交已停用。' : connection !== 'connected' ? '等待任务服务连接后发送指令。' : selected && !state ? '正在恢复任务记录。' : pendingVerifications.length ? '请先完成操作核验，再继续处理任务。' : state?.can_queue ? '新指令将进入队列。' : state?.waiting_approvals ? '请先处理待审批的操作。' : state?.waiting_questions ? '请先回答员工的问题。' : selected && !state?.can_send ? '当前任务暂时无法接收新指令。' : 'Enter 发送 · Shift+Enter 换行'}</small><small>{identity?.demo ? '演示身份' : '当前身份'} · {identity?.name ?? '正在验证'}</small></div>
          </div>
        </main>}

        {showDetails && page === 'tasks' && (
          <aside className="run-details" id="run-details" aria-label="运行详情">
            <div className="panel-heading">
              <h2>运行详情</h2>
              <Tooltip title="收起运行详情"><Button ref={detailsCloseButton} type="text" size="small" icon={<CloseOutlined />} onClick={toggleDetails} aria-label="收起运行详情" /></Tooltip>
            </div>
            <Details mode="history" events={currentRunEvents} action={(path, body) => void action(path, body)} busy={busy} canManage={identity?.role !== 'member' && diagnostic.mode === 'normal'} />
          </aside>
        )}
      </div>
    </div>
  )
}
