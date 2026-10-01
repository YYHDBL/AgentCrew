import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('agentcrew', {
  getBackendPort: (): Promise<number | null> => ipcRenderer.invoke('backend-port'),
  getToken: (): Promise<string | null> => ipcRenderer.invoke('backend-token'),
  selectMaterials: (kind: 'files' | 'folders'): Promise<string[]> => ipcRenderer.invoke('select-materials', kind),
  notify: (title: string, body: string): Promise<boolean> => ipcRenderer.invoke('notify', title, body),
  openPath: (path: string): Promise<string> => ipcRenderer.invoke('open-path', path),
  setKeepAwake: (enabled: boolean): Promise<void> => ipcRenderer.invoke('keep-awake', enabled)
})
