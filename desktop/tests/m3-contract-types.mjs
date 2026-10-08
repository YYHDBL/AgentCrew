import assert from 'node:assert/strict'
import { mkdir, readFile } from 'node:fs/promises'
import { build } from 'esbuild'
import { pathToFileURL } from 'node:url'
import { resolve } from 'node:path'

const output = resolve('../data/m3-intermediate/10-desktop/events.mjs')
await mkdir(resolve('../data/m3-intermediate/10-desktop'), { recursive: true })
await build({ entryPoints: ['src/renderer/src/api/events.ts'], outfile: output, bundle: true, platform: 'node', format: 'esm' })
const { parseEventFrame } = await import(pathToFileURL(output).href)
const evidence = JSON.parse(await readFile('../docs/acceptance/assets/M3/08/reviews-sql.json', 'utf8'))
const frames = evidence.queries.flatMap((entry) => entry.rows.filter((row) => row.global_seq && row.type && row.payload)
  .map((row) => ({ global_seq: row.global_seq, type: row.type, payload: row.payload, ts: row.created_at,
    ...(row.task_run_id ? { task_run_id: row.task_run_id, seq: row.seq } : {}),
    ...(row.attempt_no ? { attempt_no: row.attempt_no } : {}) })))
assert.ok(frames.length > 0)
for (const frame of frames) assert.equal(parseEventFrame(JSON.stringify(frame)).global_seq, frame.global_seq)
assert.equal(parseEventFrame(JSON.stringify({ ...frames[0], type: 'future.unregistered_event' })), null)
assert.throws(() => parseEventFrame(JSON.stringify({ ...frames[0], global_seq: 'invalid' })), /事件契约不符/)
assert.throws(() => parseEventFrame(JSON.stringify({ ...frames[0], type: 'tool.prepared', payload: {} })), /事件契约不符/)
console.log(JSON.stringify({ actual_frames: frames.length, unknown_ignored: true, invalid_known_rejected: true }))
