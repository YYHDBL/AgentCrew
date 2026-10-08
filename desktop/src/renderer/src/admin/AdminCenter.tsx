import { Governance, type GovernanceProps } from '../Governance'

export function AdminCenter(props: Omit<GovernanceProps, 'view' | 'embedded'>): JSX.Element {
  return <Governance {...props} view="admin" />
}
