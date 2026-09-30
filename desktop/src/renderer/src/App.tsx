import { useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { Button } from 'antd'

type NarrowPanel = 'tasks' | 'details' | null

export default function App(): JSX.Element {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [taskListOpen, setTaskListOpen] = useState(true)
  const [detailsOpen, setDetailsOpen] = useState(true)
  const [narrow, setNarrow] = useState(() => window.innerWidth < 1100)
  const [narrowPanel, setNarrowPanel] = useState<NarrowPanel>(null)
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'reconnecting'>('connecting')
  const sidebarButton = useRef<HTMLButtonElement>(null)
  const sidebarCloseButton = useRef<HTMLButtonElement>(null)
  const taskButton = useRef<HTMLButtonElement>(null)
  const taskCloseButton = useRef<HTMLButtonElement>(null)
  const detailsButton = useRef<HTMLButtonElement>(null)
  const detailsCloseButton = useRef<HTMLButtonElement>(null)

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
          <Button type="primary" disabled title="任务服务尚未接入">＋ 新建任务</Button>
        </div>
      </header>

      <div className="workspace">
        {sidebarOpen && (
          <nav className="navigation" aria-label="页面导航">
            <span className="nav-section">工作空间</span>
            <span className="nav-current" aria-current="page" title="工作台">工作台</span>
            <div className="nav-bottom">
              <Button ref={sidebarCloseButton} type="text" onClick={toggleSidebar} aria-label="收起侧栏">收起侧栏</Button>
            </div>
          </nav>
        )}

        {showTasks && (
          <aside className="task-list" id="task-list" aria-label="任务列表">
            <div className="panel-heading">
              <h2>最近任务</h2>
              <Button ref={taskCloseButton} type="text" size="small" onClick={toggleTasks} aria-label="收起任务列表">收起</Button>
            </div>
            <div className="panel-empty">
              <span className="empty-mark" aria-hidden="true">◫</span>
              <p>任务尚未加载</p>
              <small>接入任务服务后显示真实任务。</small>
            </div>
          </aside>
        )}

        <main className="task-content">
          <div className="content-scroll">
            <div className="task-heading">
              <h1>给数字员工交代一项工作</h1>
              <span className="connection-label" role="status">{connection === 'connected' ? '任务服务已连接' : connection === 'reconnecting' ? '任务服务正在重新连接' : '正在连接任务服务'}</span>
            </div>
            <div className="notice" role="status">
              工作台布局已就绪。任务创建与执行将在接入本地服务后开放。
            </div>
            <div className="empty-workspace">
              <div className="empty-symbol" aria-hidden="true">＋</div>
              <h2>工作台尚无任务内容</h2>
              <p>连接本地任务服务后，这里会显示指令、执行过程与真实产物。</p>
            </div>
          </div>
          <div className="composer-area">
            <div className="scope-line">
              <span>工作空间：尚未接入</span>
              <span>数字员工：尚未接入</span>
              <span>文件访问范围：尚未接入</span>
            </div>
            <div className="composer">
              <textarea aria-label="任务指令" disabled placeholder="接入任务服务后即可输入指令" />
              <div className="composer-actions">
                <div>
                  <Button disabled title="任务服务尚未接入">添加文件</Button>
                  <Button disabled title="任务服务尚未接入">选择文件夹</Button>
                </div>
                <Button type="primary" disabled title="任务服务尚未接入">发送任务</Button>
              </div>
            </div>
          </div>
        </main>

        {showDetails && (
          <aside className="run-details" id="run-details" aria-label="运行详情">
            <div className="panel-heading">
              <h2>运行详情</h2>
              <Button ref={detailsCloseButton} type="text" size="small" onClick={toggleDetails} aria-label="收起运行详情">收起</Button>
            </div>
            <div className="panel-empty">
              <span className="empty-mark" aria-hidden="true">◷</span>
              <p>暂无运行记录</p>
              <small>选择实际任务后显示步骤和工具结果。</small>
            </div>
          </aside>
        )}
      </div>
    </div>
  )
}
