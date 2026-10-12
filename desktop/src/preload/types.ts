import type { components } from '../renderer/src/api/generated/http'

export interface NotificationTarget {
  id: string
  job_id: string | null
  task_run_id: string | null
}

export interface DesktopInfo {
  dataDir: string
  powerLog: string
}

export interface ExitRequest {
  source: 'menu' | 'tray' | 'close' | 'system'
  impact: components['schemas']['ExitImpact'] | null
  impactError: string
}

export interface AgentCrewDesktopApi {
  getBackendPort(): Promise<number | null>
  getToken(): Promise<string | null>
  restartBackend(): Promise<void>
  selectMaterials(kind: 'files' | 'folders'): Promise<string[]>
  openPath(path: string): Promise<string>
  presentNotification(request: { title: string; body: string; notification: NotificationTarget }): Promise<{ delivered: boolean; reason?: string }>
  getDesktopInfo(): Promise<DesktopInfo>
  desktopPreferencesChanged(): Promise<void>
  confirmExit(): Promise<void>
  cancelExit(): Promise<void>
  onNotificationClick(listener: (notification: NotificationTarget) => void): () => void
  onExitConfirm(listener: (request: ExitRequest) => void): () => void
}
