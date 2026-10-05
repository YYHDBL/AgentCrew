import assert from 'node:assert/strict'
import { _electron as electron } from 'playwright-core'
import { cp, mkdir, readFile, writeFile, stat } from 'node:fs/promises'
import { resolve, basename } from 'node:path'
import { execFileSync } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { DatabaseSync } from 'node:sqlite'

const directory = resolve('.artifacts', `m1-compound-${Date.now()}`)
const conversationId = '6018b39c95f9457c8836930dba028b9f'
await mkdir(directory, { recursive: true })
await cp(resolve('../data/m1-compression-validation-03'), `${directory}/data`, { recursive: true,
  filter: (path) => !['instance.lock', 'requests.jsonl', 'streams.jsonl', 'main-streams.jsonl', 'service.log'].includes(basename(path)) })
execFileSync(resolve('../backend/.venv/bin/python'), [resolve('../scripts/seed/m1_memory_data.py'), '--data-dir', `${directory}/data`,
  '--conversation-id', conversationId, '--output', `${directory}/materials.json`], { encoding: 'utf8' })
const materials = JSON.parse(await readFile(`${directory}/materials.json`, 'utf8'))
const db = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
const checkpoints = db.prepare('SELECT id,event_global_seq,source_global_seq,before_tokens,after_tokens,sha256 FROM context_checkpoints WHERE conversation_id=? ORDER BY event_global_seq').all(conversationId)
assert.ok(checkpoints.length >= 2)
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`],
  executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'),
  env: { ...process.env, PYTHONPATH: `${resolve('../scripts/memory')}:${resolve('../backend')}`,
    MEMORY_REQUEST_EVIDENCE: `${directory}/requests.jsonl`, MEMORY_MAIN_STREAM_EVIDENCE: `${directory}/main-streams.jsonl`,
    MEMORY_RAW_RESPONSE_EVIDENCE: `${directory}/responses.jsonl` } })
const page = await app.firstWindow()
page.setDefaultTimeout(180000)
await page.emulateMedia({ reducedMotion: 'reduce' })
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
const output = { directory, conversation_id: conversationId, checkpoints, screenshots: [] }
const get = (path) => page.evaluate(async (path) => {
  const port = await window.agentcrew.getBackendPort()
  const token = await window.agentcrew.getToken()
  const response = await fetch(`http://127.0.0.1:${port}/api${path}`, { headers: { Authorization: `Bearer ${token}` } })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return (await response.json()).data
}, path)
const waitFor = async (read, predicate, milliseconds = 240000) => {
  const end = Date.now() + milliseconds
  while (Date.now() < end) {
    const result = await read()
    if (predicate(result)) return result
    await new Promise((done) => setTimeout(done, 1))
  }
  throw new Error('真实复合任务未在期限内满足条件')
}
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex')
const signature = async (path) => ({ sha256: sha(await readFile(path)), mtime_ns: String((await stat(path, { bigint: true })).mtimeNs) })
try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  const conversation = (await get('/conversations')).find((item) => item.id === conversationId)
  const title = conversation.title || await page.evaluate((date) => `任务 ${new Date(date).toLocaleString('zh-CN')}`, conversation.last_activity_at)
  await page.locator('.recent-tasks button').filter({ hasText: title }).click()
  const snapshotPath = `${directory}/data/conversations/${conversationId}/memory-snapshot.json`
  const snapshotBefore = await signature(snapshotPath)
  const marker = randomUUID()
  const operations = materials.entries.map((entry) => ({ action: 'archive', entry_hash: entry.entry_hash }))
  const instruction = `复合恢复核验 ${marker}。请先调用 memory_write target=workspace action=read 获取最新修订，再调用 memory_write action=batch 将以下60条临时核验材料归档，basis为所有者明确归档真实临时核验材料，operations=${JSON.stringify(operations)}。仅归档这60条，保留其他材料。归档成功后调用 write_file 写入工作空间 m1-13/compound-once.txt，内容恰好为 M1 compound recovered once，然后直接说明归档和文件写入的实际结果。若中断恢复，依据副作用账本及已提交变更继续未完成步骤，已归档材料和已成功写入文件保持完整，避免重复副作用。`
  await page.getByRole('textbox', { name: '任务指令', exact: true }).fill(instruction)
  await page.getByRole('button', { name: '发送任务', exact: true }).click()
  const task = await waitFor(() => get(`/conversations/${conversationId}/task-runs`),
    (rows) => rows.some((item) => item.instruction.includes(marker))).then((rows) => rows.find((item) => item.instruction.includes(marker)))
  output.task_run_id = task.id
  const oldPort = await page.evaluate(() => window.agentcrew.getBackendPort())
  const line = execFileSync('ps', ['-axo', 'pid,command'], { encoding: 'utf8' }).split('\n')
    .find((line) => /\/python(?:3(?:\.12)?)? -m agentcrew_server/.test(line) && line.includes(directory))
  assert.ok(line)
  const pid = Number(line.trim().split(/\s+/)[0])
  const intent = await waitFor(async () => {
    const state = db.prepare('SELECT status FROM task_runs WHERE id=?').get(task.id)
    if (state.status === 'failed') throw new Error('真实模型请求失败，请检查任务事件和原始响应证据')
    const approvals = page.locator('.approval').getByRole('button', { name: '允许', exact: true }).first()
    if (await approvals.count() && await approvals.isEnabled()) await approvals.click()
    return db.prepare("SELECT change_id,status,plan FROM memory_changes WHERE status='prepared' AND json_extract(plan,'$.identity.task_run_id')=? ORDER BY created_at DESC LIMIT 1").get(task.id)
  }, Boolean)
  process.kill(pid, 'SIGKILL')
  await page.getByRole('button', { name: '恢复', exact: true }).waitFor()
  await waitFor(() => page.evaluate(() => window.agentcrew.getBackendPort()), (port) => port && port !== oldPort)
  const repaired = db.prepare('SELECT status,result FROM memory_changes WHERE change_id=?').get(intent.change_id)
  assert.equal(repaired.status, 'committed')
  const repairedMemory = await signature(materials.memory_path)
  assert.equal(db.prepare("SELECT COUNT(*) AS count FROM memory_entries WHERE store_type='workspace' AND text LIKE '贯穿核验临时材料%' AND state='archived'").get().count, 60)
  await page.screenshot({ path: `${directory}/memory-interrupted.png` })
  output.screenshots.push(`${directory}/memory-interrupted.png`)
  await page.getByRole('button', { name: '恢复', exact: true }).click()
  await waitFor(async () => {
    const approval = page.locator('.approval').getByRole('button', { name: '允许', exact: true }).first()
    if (await approval.count() && await approval.isEnabled()) await approval.click()
    const questions = await get(`/conversations/${conversationId}/questions`)
    if (questions.length) throw new Error('真实模型需要额外回答，请检查该任务问题记录')
    return get(`/conversations/${conversationId}/task-runs`)
  }, (rows) => rows.some((row) => row.id === task.id && ['completed', 'failed'].includes(row.status)))
  const final = db.prepare('SELECT status,current_attempt_no FROM task_runs WHERE id=?').get(task.id)
  assert.equal(final.status, 'completed')
  assert.equal(final.current_attempt_no, 2)
  assert.deepEqual(await signature(materials.memory_path), repairedMemory)
  assert.deepEqual(await signature(snapshotPath), snapshotBefore)
  const file = `${directory}/data/workspaces/default/m1-13/compound-once.txt`
  assert.equal((await readFile(file, 'utf8')).trim(), 'M1 compound recovered once')
  const ledger = db.prepare('SELECT id,change_id,global_seq,audit_seq,source FROM memory_ledger WHERE change_id=?').all(intent.change_id)
  assert.equal(ledger.length, 1)
  const eventCount = db.prepare("SELECT COUNT(*) AS count FROM run_events WHERE type='memory.archived' AND json_extract(payload,'$.change_id')=?").get(intent.change_id).count
  assert.equal(eventCount, 1)
  const writes = db.prepare("SELECT call_id,status,dispatched_at,completed_at,input_hash FROM tool_calls WHERE task_run_id=? AND tool_name='write_file'").all(task.id)
  assert.equal(writes.length, 1)
  assert.equal(writes[0].status, 'completed')
  for (const type of ['tool.dispatched', 'tool.completed']) {
    assert.equal(db.prepare('SELECT COUNT(*) AS count FROM run_events WHERE task_run_id=? AND type=? AND json_extract(payload,\'$.call_id\')=?')
      .get(task.id, type, writes[0].call_id).count, 1)
  }
  const fileBeforeRefresh = await signature(file)
  const events = (await get(`/task-runs/${task.id}/events?after_seq=0&limit=500`)).items
  await page.reload()
  await page.getByRole('heading', { name: '已恢复 · 第 2 次尝试', exact: true }).last().waitFor()
  assert.deepEqual(await signature(file), fileBeforeRefresh)
  await page.screenshot({ path: `${directory}/compound-completed.png` })
  output.screenshots.push(`${directory}/compound-completed.png`)
  output.result = { observed_prepared: { change_id: intent.change_id, status: intent.status }, killed_pid: pid,
    old_port: oldPort, new_port: await page.evaluate(() => window.agentcrew.getBackendPort()), final,
    ledger, memory_event_count: eventCount, write_file_side_effects: writes.length, write_file_ledger: writes, memory_after_recovery: repairedMemory,
    snapshot_before: snapshotBefore, snapshot_after: await signature(snapshotPath), file: await signature(file),
    events: events.map(({ global_seq, seq, type, payload }) => ({ global_seq, seq, type,
      ...(type === 'context.budget_checked' || type === 'context.compacted' ? { payload } : {}) })) }
  assert.deepEqual(errors, [])
  output.all_checks_passed = true
  console.log(JSON.stringify({ directory, task_run_id: task.id, change_id: intent.change_id, all_checks_passed: true }))
} finally {
  await writeFile(`${directory}/compound-output.json`, JSON.stringify(output, null, 2))
  db.close()
  await app.close()
}
