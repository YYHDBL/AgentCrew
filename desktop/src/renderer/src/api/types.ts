import type { components, operations } from './generated/http'

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
