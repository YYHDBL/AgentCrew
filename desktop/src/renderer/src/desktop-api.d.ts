interface AgentCrewDesktopApi {
  getBackendPort(): Promise<number | null>
  getToken(): Promise<string | null>
  selectMaterials(kind: 'files' | 'folders'): Promise<string[]>
  notify(title: string, body: string): Promise<boolean>
  openPath(path: string): Promise<string>
  setKeepAwake(enabled: boolean): Promise<void>
}

interface Window { agentcrew: AgentCrewDesktopApi }
