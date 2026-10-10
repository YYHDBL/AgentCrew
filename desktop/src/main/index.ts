import { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, Notification, powerMonitor, powerSaveBlocker, shell, Tray } from 'electron'
import { appendFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'
import { Sidecar, appendLaunchLog } from './sidecar'
import appIconPath from '../../assets/brand/agentcrew-icon.png?asset'

interface Preferences { close_to_quit: boolean; prevent_sleep: boolean }
interface ImpactJob { id: string; name: string; state?: { next_run_at?: number | null } }
interface ImpactRun { id: string; status?: string }
interface ExitImpact {
  active_runs?: ImpactRun[]; pending_verifications?: unknown[]; upcoming_jobs?: ImpactJob[]
  background_jobs?: { id?: string; kind?: string; status?: string }[]; at_global_seq?: number; observed_at?: string
}
interface ShownNotification { notification: Notification; detail: { id: string; job_id: string | null; task_run_id: string | null }; deliveredAt: string }

declare global {
  var agentcrewLifecycle: {
    requestExit(source: 'menu' | 'tray' | 'close' | 'system'): void
    tray(): Tray | null
    shownNotifications(): ShownNotification[]
  }
}

process.on('unhandledRejection', (reason) => {
  appendLaunchLog(`${new Date().toISOString()} unhandledRejection ${String(reason)}\n`)
})
process.on('uncaughtException', (error) => {
  // 未捕获异常后监管状态不可信：记日志后退出（后端由 --parent-pid 看护收尾）。
  appendLaunchLog(`${new Date().toISOString()} uncaughtException ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`)
  app.exit(1)
})

if (!app.requestSingleInstanceLock()) app.quit()
else {
  let window: BrowserWindow | null = null
  let tray: Tray | null = null
  let quitting = false
  let exitReady = false
  // idle → confirming（等待确认）→ stopping（停止后端）→ ready（允许退出）
  let exitState: 'idle' | 'confirming' | 'stopping' | 'ready' = 'idle'
  let pendingExitSource: string | null = null
  let confirmTimeout: NodeJS.Timeout | null = null
  let blocker: number | null = null
  let preferences: Preferences = { close_to_quit: false, prevent_sleep: true }
  let preferencesPort: number | null = null
  let pendingPreferencesLoads = 0
  let preferencesLoadId = 0
  const shownNotifications: ShownNotification[] = []
  let sidecar: Sidecar

  const showWindow = (): void => {
    if (!window || window.isDestroyed()) return
    window.show()
    window.focus()
  }

  const appendStateLog = (name: string, entry: Record<string, unknown>): void => {
    try {
      appendFileSync(join(app.getPath('userData'), name), JSON.stringify({ recorded_at: new Date().toISOString(), ...entry }) + '\n')
    } catch { /* 磁盘不可写时无处可记。 */ }
  }

  const backendFetch = async (path: string, init?: RequestInit): Promise<unknown> => {
    const port = sidecar.getBackendPort()
    const token = sidecar.getToken()
    if (port === null || token === null) throw new Error('任务服务尚未就绪')
    const response = await fetch(`http://127.0.0.1:${port}/api${path}`, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      signal: AbortSignal.timeout(10000)
    })
    const body = (await response.json()) as { data?: unknown; error?: { message?: string } }
    if (!response.ok) throw new Error(body.error?.message ?? `请求失败（${response.status}）`)
    return body.data
  }

  const applyPreferences = (): void => {
    const wanted = preferences.prevent_sleep
    if (wanted && blocker !== null && !powerSaveBlocker.isStarted(blocker)) blocker = null
    if (wanted && blocker === null) blocker = powerSaveBlocker.start('prevent-app-suspension')
    if (!wanted && blocker !== null) { powerSaveBlocker.stop(blocker); blocker = null }
  }

  const loadPreferences = async (): Promise<void> => {
    const port = sidecar.getBackendPort()
    const loadId = ++preferencesLoadId
    pendingPreferencesLoads += 1
    try {
      const loaded = await backendFetch('/settings/desktop') as Preferences
      if (port !== sidecar.getBackendPort()) throw new Error('任务服务已重新启动，请重新保存桌面设置')
      if (loadId !== preferencesLoadId) return
      preferences = loaded
      applyPreferences()
      preferencesPort = port
    } finally { pendingPreferencesLoads -= 1 }
  }

  const recordPowerEvent = async (event: 'suspend' | 'resume'): Promise<void> => {
    const observedAt = new Date().toISOString()
    try {
      const data = await backendFetch('/runtime/power-event', { method: 'POST', body: JSON.stringify({ event, observed_at: observedAt }) }) as { global_seq: number }
      appendStateLog('power-events.log', { event, observed_at: observedAt, global_seq: data.global_seq })
    } catch (error) {
      appendStateLog('power-events.log', { event, observed_at: observedAt, error: error instanceof Error ? error.message : String(error) })
    }
  }

  const resetExit = (): void => {
    if (confirmTimeout) clearTimeout(confirmTimeout)
    confirmTimeout = null
    exitState = 'idle'
    quitting = false
    pendingExitSource = null
  }

  const cancelExit = (): void => {
    if (exitState !== 'confirming') return
    const source = pendingExitSource
    resetExit()
    // 关窗触发的确认被取消：回到托盘驻留（与默认关窗行为一致）
    if (source === 'close' && window && !window.isDestroyed()) window.hide()
  }

  const performQuit = (): void => {
    exitState = 'stopping'
    void (async () => {
      if (sidecar && !(await sidecar.stop())) {
        resetExit()
        void dialog.showMessageBox({ type: 'error', title: '无法退出 AgentCrew', message: '后端进程未能在清理期限内退出。', detail: `日志：${sidecar.logPath}` })
        return
      }
      if (blocker !== null) { powerSaveBlocker.stop(blocker); blocker = null }
      exitReady = true
      app.quit()
    })()
  }

  const confirmLaterNatively = (impact: ExitImpact | null, impactError: string): void => {
    const summary = impact
      ? `活跃任务 ${impact.active_runs?.length ?? 0} 项，待核验 ${impact.pending_verifications?.length ?? 0} 项，后台作业 ${impact.background_jobs?.length ?? 0} 项，未来 24 小时计划 ${impact.upcoming_jobs?.length ?? 0} 项（SQL 水位 ${impact.at_global_seq}）。`
      : `无法读取退出影响：${impactError || '任务服务未连接'}`
    const choice = dialog.showMessageBoxSync({
      type: 'warning', buttons: ['取消', '退出'], defaultId: 0, cancelId: 0,
      title: '退出 AgentCrew', message: '退出后正在执行的任务、等待审批与定时计划都会停止。',
      detail: `${summary}\n应用真正退出及电脑关机期间，到期计划按错过登记且不补跑。`
    })
    if (choice !== 1) { resetExit(); return }
    performQuit()
  }

  // 统一退出入口：菜单、托盘、关窗（close_to_quit）与系统退出共用。
  const requestExit = (source: 'menu' | 'tray' | 'close' | 'system'): void => {
    if (exitState !== 'idle') return
    exitState = 'confirming'
    pendingExitSource = source
    quitting = true
    void (async () => {
      let impact: ExitImpact | null = null
      let impactError = ''
      try { impact = await backendFetch('/runtime/exit-impact') as ExitImpact }
      catch (error) { impactError = error instanceof Error ? error.message : String(error) }
      if (exitState !== 'confirming') return
      if (!window || window.isDestroyed() || window.webContents.isCrashed()) {
        confirmLaterNatively(impact, impactError)
        return
      }
      showWindow()
      window.webContents.send('agentcrew:exit-confirm', { source, impact, impactError })
      confirmTimeout = setTimeout(() => cancelExit(), 60000)
    })()
  }

function createWindow(): void {
  window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 760,
    minHeight: 600,
    backgroundColor: '#F8FAFD',
    title: 'AgentCrew',
    icon: appIconPath,
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 20, y: 18 },
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })

  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event) => event.preventDefault())
  window.on('close', (event) => {
    if (quitting) return
    event.preventDefault()
    if (preferences.close_to_quit) requestExit('close')
    else window?.hide()
  })
  window.on('closed', () => { window = null })

  if (process.env.ELECTRON_RENDERER_URL) {
    void window.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void window.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.on('second-instance', showWindow)
app.whenReady().then(() => {
  const applicationIcon = nativeImage.createFromPath(appIconPath)
  if (applicationIcon.isEmpty()) throw new Error('AgentCrew 应用图标无法读取')
  if (process.platform === 'darwin') app.dock!.setIcon(applicationIcon)
  sidecar = new Sidecar()
  createWindow()
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: 'AgentCrew', submenu: [{ role: 'about' }, { type: 'separator' }, { label: '退出 AgentCrew', accelerator: 'CommandOrControl+Q', click: () => requestExit('menu') }] },
    { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' }
  ]))
  tray = new Tray(nativeImage.createFromNamedImage('NSImageNameActionTemplate'))
  tray.setToolTip('AgentCrew')
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '打开主窗口', click: showWindow },
    { label: '任务服务状态请查看主窗口', enabled: false },
    { type: 'separator' },
    { label: '退出 AgentCrew', click: () => requestExit('tray') }
  ]))
  tray.on('click', showWindow)
  // 真实电源事件（F010）：持久化 observed_at 与事件水位；resume 由后端重新核查到期。
  powerMonitor.on('suspend', () => void recordPowerEvent('suspend'))
  powerMonitor.on('resume', () => void recordPowerEvent('resume'))
  // 偏好以后端持久值为准：就绪时加载，sidecar 重启后重新加载。
  void setInterval(() => {
    const port = sidecar?.getBackendPort()
    if (port === null || port === preferencesPort || pendingPreferencesLoads > 0) return
    void loadPreferences().catch((error: unknown) => {
      appendLaunchLog(`${new Date().toISOString()} preferences.load.failed ${error instanceof Error ? error.message : String(error)}\n`)
    })
  }, 1000)
  const fromWindow = (event: Electron.IpcMainInvokeEvent): boolean => !!window && !window.isDestroyed() && event.sender.id === window.webContents.id && !event.sender.isDestroyed()
  ipcMain.handle('backend-port', (event) => { if (!fromWindow(event)) throw new Error('无效的调用来源'); return sidecar.getBackendPort() })
  ipcMain.handle('backend-token', (event) => { if (!fromWindow(event)) throw new Error('无效的调用来源'); return sidecar.getToken() })
  ipcMain.handle('restart-backend', async (event) => {
    if (!fromWindow(event)) throw new Error('无效的调用来源')
    await sidecar.restart()
  })
  ipcMain.handle('desktop-info', (event) => {
    if (!fromWindow(event)) throw new Error('无效的调用来源')
    return { dataDir: sidecar.dataDir, powerLog: join(app.getPath('userData'), 'power-events.log') }
  })
  ipcMain.handle('desktop-preferences-changed', async (event) => {
    if (!fromWindow(event)) throw new Error('无效的调用来源')
    await loadPreferences()
  })
  ipcMain.handle('exit-confirm', (event) => {
    if (!fromWindow(event) || exitState !== 'confirming') return
    if (confirmTimeout) clearTimeout(confirmTimeout)
    confirmTimeout = null
    performQuit()
  })
  ipcMain.handle('exit-cancel', (event) => {
    if (!fromWindow(event)) return
    cancelExit()
  })
  ipcMain.handle('select-materials', async (event, kind: unknown) => {
    if (!fromWindow(event) || (kind !== 'files' && kind !== 'folders')) throw new Error('材料选择参数无效')
    const result = await dialog.showOpenDialog(window!, { properties: kind === 'files' ? ['openFile', 'multiSelections'] : ['openDirectory', 'multiSelections'] })
    return result.canceled ? [] : result.filePaths
  })
  ipcMain.handle('present-notification', (event, payload: unknown) => {
    if (!fromWindow(event) || typeof payload !== 'object' || payload === null) throw new Error('通知参数无效')
    const request = payload as { title?: unknown; body?: unknown; notification?: unknown }
    const detail = request.notification
    if (typeof request.title !== 'string' || typeof request.body !== 'string' || request.title.length === 0 || request.title.length > 120 || request.body.length > 1000
      || typeof detail !== 'object' || detail === null || typeof (detail as { id?: unknown }).id !== 'string' || (detail as { id: string }).id.length > 200
      || !((detail as { job_id?: unknown }).job_id === null || typeof (detail as { job_id?: unknown }).job_id === 'string')
      || !((detail as { task_run_id?: unknown }).task_run_id === null || typeof (detail as { task_run_id?: unknown }).task_run_id === 'string')) throw new Error('通知参数无效')
    const identity = { id: (detail as { id: string }).id, job_id: (detail as { job_id: string | null }).job_id ?? null, task_run_id: (detail as { task_run_id: string | null }).task_run_id ?? null }
    if (!Notification.isSupported()) {
      appendStateLog('notifications.log', { notification_id: identity.id, delivered: false, reason: 'unsupported' })
      return { delivered: false, reason: '系统不支持桌面通知' }
    }
    const notice = new Notification({ title: request.title, body: request.body })
    notice.on('click', () => {
      showWindow()
      if (window && !window.isDestroyed()) window.webContents.send('agentcrew:notification-click', identity)
    })
    shownNotifications.push({ notification: notice, detail: identity, deliveredAt: new Date().toISOString() })
    if (shownNotifications.length > 50) shownNotifications.splice(0, shownNotifications.length - 50)
    notice.show()
    appendStateLog('notifications.log', { notification_id: identity.id, delivered: true, severity_path: 'electron-Notification.show' })
    return { delivered: true }
  })
  ipcMain.handle('open-path', async (event, path: unknown) => {
    if (!fromWindow(event) || typeof path !== 'string' || !isAbsolute(path)) throw new Error('文件路径无效')
    // 只定位不执行：openPath 会以默认应用启动目标（macOS 上含 .app 包），
    // 超出最小必要特权面；reveal 在 Finder 里选中，满足"找到产物"的需要。
    shell.showItemInFolder(path)
    return ''
  })
  // 真实验收入口（只读暴露真实对象与统一函数，不新增行为）。
  globalThis.agentcrewLifecycle = {
    requestExit: (source: 'menu' | 'tray' | 'close' | 'system') => requestExit(source),
    tray: () => tray,
    shownNotifications: () => shownNotifications.slice()
  }
  void sidecar.start()
  app.on('activate', showWindow)
})

app.on('before-quit', (event) => {
  if (exitReady) return
  event.preventDefault()
  requestExit('system')
})
app.on('window-all-closed', () => { /* 托盘维持应用与后端运行。 */ })
}
