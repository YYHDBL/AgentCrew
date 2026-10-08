import type { components, operations, paths } from './generated/http'

export type RunRecord = components['schemas']['RunRecord']
export type CronJob = components['schemas']['CronJob']
export type CronOccurrence = components['schemas']['CronOccurrence']
export type TraceJob = components['schemas']['TraceJob']
export type TraceReport = components['schemas']['TraceReport']
export type ScheduleProposal = components['schemas']['ScheduleProposal']
export type CronCreateRequest = components['schemas']['CronCreateRequest']
export type ExitImpact = components['schemas']['ExitImpact']
export type Notification = components['schemas']['Notification']
export type Identity = components['schemas']['Identity']
export type AgentSpec = components['schemas']['AgentSpec']
export type Operation = keyof operations
export type RunMetrics = components['schemas']['RunMetrics']
export type RunCall = components['schemas']['RunCall']
export type RunEventsPage = operations['readRunEvents']['responses'][200]['content']['application/json']['data']
export type RunAttempts = NonNullable<paths['/api/task-runs/{id}/attempts']['get']['responses'][200]['content']['application/json']['data']>
