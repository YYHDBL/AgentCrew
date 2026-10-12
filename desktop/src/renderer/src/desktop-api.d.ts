import type { AgentCrewDesktopApi } from '../../preload/types'

declare global {
  interface Window { agentcrew: AgentCrewDesktopApi }
}
