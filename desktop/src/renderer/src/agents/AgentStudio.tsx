import { Governance, type GovernanceProps } from '../Governance'

export function AgentStudio(props: Omit<GovernanceProps, 'view' | 'embedded'>): JSX.Element {
  return <Governance {...props} view="studio" />
}
