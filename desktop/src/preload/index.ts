import { contextBridge, ipcRenderer } from 'electron'
import type { AgentCrewDesktopApi, ExitRequest, NotificationTarget } from './types'

const desktopApi: AgentCrewDesktopApi = {
  getBackendPort: (): Promise<number | null> => ipcRenderer.invoke('backend-port'),
  getToken: (): Promise<string | null> => ipcRenderer.invoke('backend-token'),
  restartBackend: (): Promise<void> => ipcRenderer.invoke('restart-backend'),
  selectMaterials: (kind: 'files' | 'folders'): Promise<string[]> => ipcRenderer.invoke('select-materials', kind),
  openPath: (path: string): Promise<string> => ipcRenderer.invoke('open-path', path),
  presentNotification: (request) => ipcRenderer.invoke('present-notification', request),
  getDesktopInfo: () => ipcRenderer.invoke('desktop-info'),
  desktopPreferencesChanged: () => ipcRenderer.invoke('desktop-preferences-changed'),
  confirmExit: () => ipcRenderer.invoke('exit-confirm'),
  cancelExit: () => ipcRenderer.invoke('exit-cancel'),
  onNotificationClick: (listener) => {
    const receive = (_event: Electron.IpcRendererEvent, notification: NotificationTarget): void => listener(notification)
    ipcRenderer.on('agentcrew:notification-click', receive)
    return () => ipcRenderer.removeListener('agentcrew:notification-click', receive)
  },
  onExitConfirm: (listener) => {
    const receive = (_event: Electron.IpcRendererEvent, request: ExitRequest): void => listener(request)
    ipcRenderer.on('agentcrew:exit-confirm', receive)
    return () => ipcRenderer.removeListener('agentcrew:exit-confirm', receive)
  }
}

contextBridge.exposeInMainWorld('agentcrew', desktopApi)
