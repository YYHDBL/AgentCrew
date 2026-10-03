import assert from 'node:assert/strict'
import { _electron as electron } from 'playwright-core'
import { cp, mkdir, readFile, writeFile, stat } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { resolve, basename } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'
import { DatabaseSync } from 'node:sqlite'

const seed = resolve('../data/m1-compression-validation-01')
const conversationId = '74ea536019444da3b4d95535622a4be7'
const directory = resolve('.artifacts', `m1-08-recovery-${Date.now()}`)
await mkdir(directory, { recursive: true })
await cp(seed, `${directory}/data`, { recursive: true, filter: (path) =>
  !['instance.lock', 'requests.jsonl', 'streams.jsonl', 'main-streams.jsonl', 'service.log'].includes(basename(path)) })
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`],
  executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const page = await app.firstWindow()
page.setDefaultTimeout(180000)
await page.emulateMedia({ reducedMotion: 'reduce' })
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
const output = { directory, conversation_id: conversationId, screenshots: [] }
const request = (path) => page.evaluate(async (path) => {
  const port = await window.agentcrew.getBackendPort()
  const token = await window.agentcrew.getToken()
  const response = await fetch(`http://127.0.0.1:${port}/api${path}`, {
    headers: { Authorization: `Bearer ${token}` } })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return (await response.json()).data
}, path)
const waitFor = async (read, accepts, seconds = 240) => {
  const deadline = Date.now() + seconds * 1000
  while (Date.now() < deadline) {
    const value = await read()
    if (accepts(value)) return value
    await new Promise((done) => setTimeout(done, 50))
  }
  throw new Error('真实桌面状态未在期限内满足验收条件')
}
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex')
let database
try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  const conversations = await request('/conversations')
  const selected = conversations.find((conversation) => conversation.id === conversationId)
  assert.ok(selected)
  const label = selected.title || await page.evaluate((date) => `任务 ${new Date(date).toLocaleString('zh-CN')}`, selected.last_activity_at)
  await page.locator('.recent-tasks button').filter({ hasText: label }).click()
  await waitFor(() => page.evaluate(() => sessionStorage.getItem('conversation')),
    (value) => value === conversationId)
  database = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
  const checkpointsBefore = database.prepare('SELECT id,event_global_seq,source_global_seq,sha256,before_tokens,after_tokens FROM context_checkpoints WHERE conversation_id=? ORDER BY event_global_seq').all(conversationId)
  assert.ok(checkpointsBefore.length >= 2)
  const snapshotPath = `${directory}/data/conversations/${conversationId}/memory-snapshot.json`
  const snapshotBefore = { sha256: sha(await readFile(snapshotPath)), mtimeMs: (await stat(snapshotPath)).mtimeMs }
  const marker = randomUUID()
  const instruction = `恢复核验标识 ${marker}。请先用 write_file 在工作空间创建 m1-08/recovered-once.txt，内容恰好为 M1-08 recovered once，然后使用 read_file 核对文件。完成文件核对后，用约2000中文字符说明已完成的源码核验方法及当前状态。中断恢复时先核对副作用账本；已经成功写入且读取一致的文件保持内容与修改时间，只继续完成说明。`
  await page.getByRole('textbox', { name: '任务指令', exact: true }).fill(instruction)
  await page.getByRole('button', { name: '发送任务', exact: true }).click()
  const run = await waitFor(() => request(`/conversations/${conversationId}/task-runs`),
    (runs) => runs.some((item) => item.instruction.includes(marker))).then((runs) =>
    runs.find((item) => item.instruction.includes(marker)))
  output.task_run_id = run.id
  const approval = page.locator('.approval').getByRole('button', { name: '允许', exact: true })
  await approval.first().waitFor()
  await approval.first().click()
  const checked = await waitFor(async () => {
    const active = page.locator('.approval').getByRole('button', { name: '允许', exact: true }).first()
    if (await active.count() && await active.isEnabled()) await active.click()
    return request(`/task-runs/${run.id}/events?after_seq=0&limit=500`)
  },
    (history) => history.items.some((event) => event.type === 'tool.completed' && event.payload.details?.total_lines))
  const scope = await request(`/conversations/${conversationId}/scope`)
  const filePath = `${scope.workspace_dir}/m1-08/recovered-once.txt`
  assert.equal((await readFile(filePath, 'utf8')).trim(), 'M1-08 recovered once')
  const beforeFile = { sha256: sha(await readFile(filePath)), mtimeMs: (await stat(filePath)).mtimeMs }
  const readEvent = checked.items.findLast((event) => event.type === 'tool.completed' && event.payload.details?.total_lines)
  const logPath = `${directory}/data/logs/sidecar.log`
  const beforeLog = (await readFile(logPath, 'utf8')).length
  await waitFor(() => request(`/task-runs/${run.id}/events?after_seq=0&limit=500`), (history) =>
    history.items.some((event) => event.type === 'llm.request_started' && event.global_seq > readEvent.global_seq))
  await waitFor(async () => (await readFile(logPath, 'utf8')).slice(beforeLog),
    (text) => text.includes('HTTP/1.1 200 OK'))
  const state = (await request(`/conversations/${conversationId}/task-runs`)).find((item) => item.id === run.id)
  assert.equal(state.status, 'running')
  const oldPort = await page.evaluate(() => window.agentcrew.getBackendPort())
  const processLine = execFileSync('ps', ['-axo', 'pid,command'], { encoding: 'utf8' }).split('\n')
    .find((line) => line.includes('/python3 -m agentcrew_server') && line.includes(directory))
  assert.ok(processLine)
  const killedPid = Number(processLine.trim().split(/\s+/)[0])
  process.kill(killedPid, 'SIGKILL')
  await page.getByRole('button', { name: '恢复', exact: true }).waitFor()
  await waitFor(() => page.evaluate(() => window.agentcrew.getBackendPort()), (port) => port && port !== oldPort)
  await page.locator('.content-scroll').evaluate((element) => { element.scrollTop = element.scrollHeight })
  await page.locator('.details-scroll').evaluate((element) => { element.scrollTop = element.scrollHeight })
  await page.screenshot({ path: `${directory}/interrupted.png` })
  output.screenshots.push(`${directory}/interrupted.png`)
  output.sigkill = { killed_pid: killedPid, old_port: oldPort,
    new_port: await page.evaluate(() => window.agentcrew.getBackendPort()),
    file_before: beforeFile }
  await page.getByRole('button', { name: '恢复', exact: true }).click()
  const terminal = await waitFor(async () => {
    const active = page.locator('.approval').getByRole('button', { name: '允许', exact: true }).first()
    if (await active.count() && await active.isEnabled()) await active.click()
    return request(`/conversations/${conversationId}/task-runs`)
  },
    (runs) => runs.some((item) => item.id === run.id && ['completed', 'failed'].includes(item.status)))
  assert.equal(terminal.find((item) => item.id === run.id).status, 'completed')
  const afterFile = { sha256: sha(await readFile(filePath)), mtimeMs: (await stat(filePath)).mtimeMs }
  assert.deepEqual(afterFile, beforeFile)
  assert.deepEqual({ sha256: sha(await readFile(snapshotPath)), mtimeMs: (await stat(snapshotPath)).mtimeMs }, snapshotBefore)
  const events = (await request(`/task-runs/${run.id}/events?after_seq=0&limit=500`)).items
  assert.ok(events.some((event) => event.type === 'run.resumed' && event.payload.attempt_no === 2))
  const writes = events.filter((event) => event.type === 'tool.prepared' && event.payload.tool_name === 'write_file')
  assert.equal(writes.length, 1)
  await page.reload()
  await page.getByRole('heading', { name: '已恢复 · 第 2 次尝试', exact: true }).last().waitFor()
  await page.locator('.content-scroll').evaluate((element) => { element.scrollTop = element.scrollHeight })
  await page.locator('.details-scroll').evaluate((element) => { element.scrollTop = element.scrollHeight })
  await page.screenshot({ path: `${directory}/completed.png` })
  output.screenshots.push(`${directory}/completed.png`)
  output.file_after = afterFile
  output.snapshot = snapshotBefore
  output.checkpoints = checkpointsBefore
  output.events = events.filter((event) => ['run.started', 'run.interrupted', 'run.resumed', 'run.completed', 'context.budget_checked', 'permission.resolved', 'tool.completed'].includes(event.type))
  output.write_side_effects = writes.length
  assert.deepEqual(errors, [])
  output.all_checks_passed = true
  console.log(JSON.stringify({ directory, task_run_id: run.id, write_side_effects: writes.length, all_checks_passed: true }))
} finally {
  await writeFile(`${directory}/recovery-output.json`, JSON.stringify(output, null, 2))
  database?.close()
  await app.close()
}
