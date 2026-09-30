import { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, Notification, powerSaveBlocker, shell, Tray } from 'electron'
import { isAbsolute, join } from 'node:path'
import { Sidecar } from './sidecar'

if (!app.requestSingleInstanceLock()) app.quit()
else {
  let window: BrowserWindow | null = null
  let tray: Tray | null = null
  let quitting = false
  let blocker: number | null = null
  let sidecar: Sidecar

  const showWindow = (): void => {
    if (!window) return
    window.show()
    window.focus()
  }

function createWindow(): void {
  window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 760,
    minHeight: 600,
    backgroundColor: '#F8FAFD',
    title: 'AgentCrew',
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
  window.on('close', (event) => { if (!quitting) { event.preventDefault(); window?.hide() } })

  if (process.env.ELECTRON_RENDERER_URL) {
    void window.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void window.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

function quitFromTray(): void {
  const choice = dialog.showMessageBoxSync({
    type: 'warning', buttons: ['取消', '退出'], defaultId: 0, cancelId: 0,
    title: '退出 AgentCrew', message: '退出后任务服务会停止。',
    detail: '如果有正在运行或等待审批的任务，退出会中断处理。'
  })
  if (choice !== 1) return
  quitting = true
  app.quit()
}

app.on('second-instance', showWindow)
app.whenReady().then(() => {
  sidecar = new Sidecar()
  createWindow()
  blocker = powerSaveBlocker.start('prevent-app-suspension')
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: 'AgentCrew', submenu: [{ role: 'about' }, { type: 'separator' }, { label: '退出 AgentCrew', accelerator: 'CommandOrControl+Q', click: quitFromTray }] },
    { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' }
  ]))
  tray = new Tray(nativeImage.createFromNamedImage('NSImageNameActionTemplate'))
  tray.setToolTip('AgentCrew')
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '打开主窗口', click: showWindow },
    { label: '任务服务状态请查看主窗口', enabled: false },
    { type: 'separator' },
    { label: '退出 AgentCrew', click: quitFromTray }
  ]))
  tray.on('click', showWindow)
  const fromWindow = (event: Electron.IpcMainInvokeEvent): boolean => event.sender.id === window?.webContents.id && !event.sender.isDestroyed()
  ipcMain.handle('backend-port', (event) => { if (!fromWindow(event)) throw new Error('无效的调用来源'); return sidecar.getBackendPort() })
  ipcMain.handle('backend-token', (event) => { if (!fromWindow(event)) throw new Error('无效的调用来源'); return sidecar.getToken() })
  ipcMain.handle('notify', (event, title: unknown, body: unknown) => {
    if (!fromWindow(event) || typeof title !== 'string' || typeof body !== 'string' || title.length > 120 || body.length > 1000) throw new Error('通知参数无效')
    if (!Notification.isSupported()) return false
    new Notification({ title, body }).show()
    return true
  })
  ipcMain.handle('open-path', async (event, path: unknown) => {
    if (!fromWindow(event) || typeof path !== 'string' || !isAbsolute(path)) throw new Error('文件路径无效')
    return shell.openPath(path)
  })
  ipcMain.handle('keep-awake', (event, enabled: unknown) => {
    if (!fromWindow(event) || typeof enabled !== 'boolean') throw new Error('休眠设置无效')
    if (enabled && blocker === null) blocker = powerSaveBlocker.start('prevent-app-suspension')
    if (!enabled && blocker !== null) { powerSaveBlocker.stop(blocker); blocker = null }
  })
  void sidecar.start()
  app.on('activate', showWindow)
})

app.on('before-quit', () => { quitting = true; sidecar?.stop(); if (blocker !== null) powerSaveBlocker.stop(blocker) })
app.on('window-all-closed', () => { /* 托盘维持应用与后端运行。 */ })
}
