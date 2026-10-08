import Ajv from 'ajv'
import schema from './generated/events.schema.json'
import type { EventFrame } from './generated/events'

const validator = new Ajv({ strict: false, allErrors: true }).compile<EventFrame>(schema)
const eventTypes = new Set<string>(schema.definitions.EventType.enum)

export function parseEventFrame(text: string): EventFrame | null {
  const value: unknown = JSON.parse(text)
  if (typeof value !== 'object' || value === null || !('type' in value) || typeof value.type !== 'string') {
    throw new Error('事件信封缺少合法类型')
  }
  if (!eventTypes.has(value.type)) return null
  if (!validator(value)) throw new Error(`事件契约不符：${validator.errors?.map((error) => `${error.instancePath} ${error.message}`).join('；')}`)
  return value
}
