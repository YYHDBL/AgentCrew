import assert from 'node:assert/strict'
import { _electron as electron } from 'playwright-core'
import { mkdir, cp, readFile, writeFile, stat } from 'node:fs/promises'
import { resolve } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'
import { createServer } from 'node:http'
import { execFileSync } from 'node:child_process'
import { DatabaseSync } from 'node:sqlite'

const directory = resolve('.artifacts', `m2-recovery-${Date.now()}`)
await mkdir(`${directory}/data`, { recursive: true })
await cp(resolve('../data/m1-final-memory-03/config.json'), `${directory}/data/config.json`)
const configuration = JSON.parse(await readFile(`${directory}/data/config.json`, 'utf8'))
const secrets = Object.values(configuration.models).map((slot) => slot.api_key).filter(Boolean)
const safe = (value) => {
  let text = JSON.stringify(value)
  for (const secret of secrets) text = text.replaceAll(secret, '***')
  return JSON.parse(text)
}
const upstream = new DatabaseSync(`${directory}/upstream.sqlite`)
upstream.exec('CREATE TABLE operations(id INTEGER PRIMARY KEY,idempotency_key TEXT UNIQUE,body TEXT); CREATE TABLE requests(id INTEGER PRIMARY KEY,idempotency_key TEXT,body TEXT)')
let heldResponse
const server = createServer(async (request, response) => {
  if (request.method === 'POST') {
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    const body = Buffer.concat(chunks).toString('utf8')
    const key = String(request.headers['idempotency-key'] ?? '')
    const existing = upstream.prepare('SELECT id FROM operations WHERE idempotency_key=?').get(key)
    upstream.exec('BEGIN IMMEDIATE')
    upstream.prepare('INSERT INTO requests(idempotency_key,body) VALUES(?,?)').run(key, body)
    upstream.prepare('INSERT OR IGNORE INTO operations(idempotency_key,body) VALUES(?,?)').run(key, body)
    upstream.exec('COMMIT')
    if (existing) {
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify({ operations: upstream.prepare('SELECT count(*) AS count FROM operations').get().count }))
      return
    }
    heldResponse = response
    return
  }
  response.setHeader('Content-Type', 'application/json')
  response.end(JSON.stringify({ operations: upstream.prepare('SELECT count(*) AS count FROM operations').get().count }))
})
await new Promise((done) => server.listen(0, '127.0.0.1', done))
const endpoint = `http://127.0.0.1:${server.address().port}`
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`], executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'),
  env: { ...process.env, UV_CACHE_DIR: resolve('../data/m2-intermediate/uv-cache') } })
const page = await app.firstWindow()
page.setDefaultTimeout(180000)
await page.emulateMedia({ reducedMotion: 'reduce' })
let db
const evidence = { directory, models: Object.fromEntries(Object.entries(configuration.models).map(([name, slot]) => [name, { provider: slot.provider, model: slot.model }])), requests: [], screenshots: [] }
const request = async (method, path, body) => {
  const observed = await page.evaluate(async ({ method, path, body }) => {
    const port = await window.agentcrew.getBackendPort()
    const token = await window.agentcrew.getToken()
    const response = await fetch(`http://127.0.0.1:${port}/api${path}`, { method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
    return { status: response.status, text: await response.text() }
  }, { method, path, body })
  const value = observed.text ? JSON.parse(observed.text) : null
  evidence.requests.push(safe({ method, path, request: body, status: observed.status, response: value }))
  return { status: observed.status, value: value?.data }
}
const waitFor = async (read, accepts, milliseconds = 180000) => {
  const deadline = Date.now() + milliseconds
  while (Date.now() < deadline) {
    const value = await read()
    if (accepts(value)) return value
    await new Promise((done) => setTimeout(done, 100))
  }
  throw new Error('真实恢复状态未在期限内满足核查条件')
}
const signature = async (path) => ({ sha256: createHash('sha256').update(await readFile(path)).digest('hex'), mtime_ns: String((await stat(path, { bigint: true })).mtimeNs) })
try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  db = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
  assert.equal((await request('POST', '/memory/stores/soul/xiaowen?workspace_id=office&agent_id=xiaowen', {
    change_id: `configure-recovery-soul-${randomUUID()}`, expected_revision: 0, basis: '所有者为治理恢复验收配置员工岗位与工作规则',
    text: '我是负责整理办公材料和核对实际输出的数字员工。执行任务时遵守当前工作区、工具授权和审批，核查真实文件、调用结果与副作用账本；中断后保留已完成工作，并根据当前授权继续合法的剩余步骤。'
  })).status, 200)
  const created = await request('POST', '/connectors', { change_id: `recovery-connector-${randomUUID()}`, expected_revision: 0, workspace_id: 'office', name: '真实恢复持久化服务', type: 'http',
    config: { url: endpoint, allowed_hosts: ['127.0.0.1'], allowed_ports: [server.address().port], allow_loopback: true,
      idempotent_endpoints: [{ method: 'POST', path: '/deduplicated', guarantee: '真实SQLite唯一幂等键事务只保存一次操作' }] } })
  assert.equal(created.status, 200)
  const grant = await request('POST', '/grants', { change_id: `recovery-grant-${randomUUID()}`, resource_type: 'connector', resource_id: created.value.id, grantee_type: 'agent', grantee_id: 'xiaowen' })
  assert.equal(grant.status, 200)
  const body = `真实恢复材料-${randomUUID()}`
  const instruction = `请先用write_file写入m2-09/retained.txt，完整正文为M2 retained file，然后调用http_request，参数为${JSON.stringify({ connector_id: created.value.id, url: `${endpoint}/deduplicated`, method: 'POST', body })}。需要审批时等待决定。收到真实HTTP结果后再用write_file写入m2-09/after.txt，正文为M2 resumed after verification。中断恢复时依据副作用账本与当前授权，已确认执行的调用保持完整，继续仍然合法的剩余文件步骤。`
  const run = await request('POST', '/conversations', { workspace_id: 'office', agent_id: 'xiaowen', instruction, client_request_id: randomUUID() })
  assert.equal(run.status, 201)
  const taskId = run.value.task_run_id
  const conversationId = run.value.conversation.id
  evidence.task_run_id = taskId
  evidence.conversation_id = conversationId
  await page.reload()
  const conversations = await request('GET', '/conversations')
  const conversation = conversations.value.find((row) => row.id === conversationId)
  const title = conversation.title || await page.evaluate((date) => `任务 ${new Date(date).toLocaleString('zh-CN')}`, conversation.last_activity_at)
  await page.locator('.recent-tasks button').filter({ hasText: title }).click()
  const marker = `${directory}/data/workspaces/office/files/m2-09/retained.txt`
  await waitFor(async () => {
    const cards = await request('GET', `/task-runs/${taskId}/approvals`)
    for (const card of cards.value) {
      if (card.stale) continue
      const call = db.prepare('SELECT tool_name,input FROM tool_calls WHERE call_id=?').get(card.call_id)
      const inputs = JSON.parse(call.input)
      const allowed = call.tool_name === 'http_request' && inputs.connector_id === created.value.id || call.tool_name === 'write_file' && inputs.path.endsWith('retained.txt')
      const result = await request('POST', `/tool-approvals/${card.call_id}`, { decision: allowed ? 'allow_once' : 'reject_once', input_hash: card.input_hash })
      assert.equal(result.status, 200)
    }
    const questions = await request('GET', `/conversations/${conversationId}/questions`)
    for (const question of questions.value) assert.equal((await request('POST', `/questions/${question.request_id}/answer`, { answer: '请按给定参数使用write_file创建m2-09/retained.txt，再使用已指定connector_id的http_request发送真实材料，等待审批。' })).status, 200)
    const count = upstream.prepare('SELECT count(*) AS count FROM operations').get().count
    return count === 1 && db.prepare("SELECT count(*) AS count FROM artifacts WHERE task_run_id=? AND path LIKE '%retained.txt' AND status='ready'").get(taskId).count === 1
  }, Boolean)
  const markerBefore = await signature(marker)
  const snapshot = `${directory}/data/conversations/${conversationId}/memory-snapshot.json`
  const snapshotBefore = await signature(snapshot)
  const bindingBefore = db.prepare('SELECT agent_spec,skill_versions FROM task_governance WHERE task_run_id=?').get(taskId)
  const oldPort = await page.evaluate(() => window.agentcrew.getBackendPort())
  const processLine = execFileSync('ps', ['-axo', 'pid,command'], { encoding: 'utf8' }).split('\n').find((line) => /\/python(?:3(?:\.12)?)? -m agentcrew_server/.test(line) && line.includes(`${directory}/data`))
  assert.ok(processLine)
  const pid = Number(processLine.trim().split(/\s+/)[0])
  evidence.killed_python_pid = pid
  evidence.old_port = oldPort
  process.kill(pid, 'SIGKILL')
  heldResponse.destroy()
  await waitFor(() => page.evaluate(() => window.agentcrew.getBackendPort()), (port) => port && port !== oldPort)
  await page.getByRole('button', { name: '恢复', exact: true }).waitFor()
  const pending = await request('GET', `/conversations/${conversationId}/pending-verifications`)
  assert.equal(pending.status, 200)
  const call = pending.value.find((row) => row.tool === 'http_request')
  assert.ok(call)
  assert.equal((await request('POST', `/task-runs/${taskId}/resume`, {})).status, 409)
  assert.equal((await request('DELETE', `/grants/${grant.value.id}`, { change_id: `revoke-recovery-${randomUUID()}`, expected_revision: grant.value.revision })).status, 200)
  assert.equal(upstream.prepare('SELECT count(*) AS count FROM operations').get().count, 1)
  assert.equal((await request('POST', `/tool-calls/${call.call_id}/verification`, { verdict: 'confirmed_executed', note: '实际查询上游SQLite，原幂等键与正文均存在，操作次数1' })).status, 200)
  const originalKey = `${taskId}:${call.call_id}`
  assert.equal(upstream.prepare('SELECT idempotency_key FROM operations').get().idempotency_key, originalKey)
  const repeated = await fetch(`${endpoint}/deduplicated`, { method: 'POST', headers: { 'Idempotency-Key': originalKey }, body })
  assert.equal(repeated.status, 200)
  assert.equal((await repeated.json()).operations, 1)
  await page.getByRole('button', { name: '恢复', exact: true }).click()
  await waitFor(async () => {
    const cards = await request('GET', `/task-runs/${taskId}/approvals`)
    for (const card of cards.value) if (!card.stale) {
      const row = db.prepare('SELECT tool_name,input FROM tool_calls WHERE call_id=?').get(card.call_id)
      const inputs = JSON.parse(row.input)
      const allowed = row.tool_name === 'write_file' && inputs.path.endsWith('after.txt')
      assert.equal((await request('POST', `/tool-approvals/${card.call_id}`, { decision: allowed ? 'allow_once' : 'reject_once', input_hash: card.input_hash })).status, 200)
    }
    const questions = await request('GET', `/conversations/${conversationId}/questions`)
    for (const question of questions.value) assert.equal((await request('POST', `/questions/${question.request_id}/answer`, { answer: '当前HTTP授权已撤销，上游操作已核验一次。请保持原文件，仅使用write_file完成m2-09/after.txt的合法文件步骤，正文为M2 resumed after verification，然后说明实际情况。' })).status, 200)
    return db.prepare('SELECT status FROM task_runs WHERE id=?').get(taskId).status
  }, (status) => ['completed', 'failed', 'waiting_verification'].includes(status))
  assert.deepEqual(await signature(marker), markerBefore)
  assert.deepEqual(await signature(snapshot), snapshotBefore)
  assert.deepEqual(db.prepare('SELECT agent_spec,skill_versions FROM task_governance WHERE task_run_id=?').get(taskId), bindingBefore)
  assert.equal(upstream.prepare('SELECT count(*) AS count FROM operations').get().count, 1)
  assert.equal(db.prepare("SELECT count(*) AS count FROM tool_calls WHERE task_run_id=? AND tool_name='http_request' AND dispatched_at IS NOT NULL").get(taskId).count, 1)
  const attempts = db.prepare('SELECT id,attempt_no,status,context_fingerprint FROM run_attempts WHERE task_run_id=? ORDER BY attempt_no').all(taskId)
  assert.equal(attempts.length, 2)
  evidence.resumed_outcome = {
    status: db.prepare('SELECT status FROM task_runs WHERE id=?').get(taskId).status,
    terminal_events: db.prepare("SELECT type,payload FROM run_events WHERE task_run_id=? AND attempt_no=2 AND type IN ('run.completed','run.failed') ORDER BY global_seq").all(taskId)
  }
  if (db.prepare("SELECT count(*) AS count FROM artifacts WHERE task_run_id=? AND path LIKE '%after.txt' AND status='ready'").get(taskId).count === 0) {
    const followup = await request('POST', `/conversations/${conversationId}/instructions`, { text: '保持已核验HTTP调用与原retained.txt完整。现在仅调用write_file，在当前工作空间写入m2-09/after.txt，正文为M2 resumed after verification。无需任何网络操作，按真实文件结果说明。', client_request_id: randomUUID() })
    assert.equal(followup.status, 202)
    evidence.legal_followup_task_run_id = followup.value.task_run_id
    await waitFor(async () => {
      const cards = await request('GET', `/task-runs/${followup.value.task_run_id}/approvals`)
      for (const card of cards.value) if (!card.stale) {
        const row = db.prepare('SELECT tool_name,input FROM tool_calls WHERE call_id=?').get(card.call_id)
        const inputs = JSON.parse(row.input)
        assert.equal((await request('POST', `/tool-approvals/${card.call_id}`, { decision: row.tool_name === 'write_file' && inputs.path.endsWith('after.txt') ? 'allow_once' : 'reject_once', input_hash: card.input_hash })).status, 200)
      }
      const questions = await request('GET', `/conversations/${conversationId}/questions`)
      for (const question of questions.value) assert.equal((await request('POST', `/questions/${question.request_id}/answer`, { answer: '仅执行当前工作区write_file，目标m2-09/after.txt，正文M2 resumed after verification；保持已完成文件和HTTP副作用。' })).status, 200)
      return db.prepare('SELECT status FROM task_runs WHERE id=?').get(followup.value.task_run_id).status
    }, (status) => ['completed', 'failed'].includes(status))
  }
  assert.equal(await readFile(`${directory}/data/workspaces/office/files/m2-09/after.txt`, 'utf8'), 'M2 resumed after verification')
  const actualRequests = db.prepare("SELECT payload FROM run_events WHERE task_run_id=? AND type='llm.request_started' AND attempt_no=2").all(taskId).map((row) => JSON.parse(row.payload))
  assert.ok(actualRequests.length)
  assert.ok(actualRequests.every((row) => !row.tool_names.includes('http_request') || db.prepare("SELECT count(*) AS count FROM tool_calls WHERE task_run_id=? AND tool_name='http_request' AND prepared_at > (SELECT started_at FROM run_attempts WHERE task_run_id=? AND attempt_no=2)").get(taskId, taskId).count === 0))
  evidence.attempts = attempts
  evidence.resumed_requests = actualRequests
  evidence.snapshot = snapshotBefore
  evidence.retained_file = markerBefore
  evidence.upstream = { operations: upstream.prepare('SELECT * FROM operations').all(), requests: upstream.prepare('SELECT * FROM requests').all() }
  evidence.final_status = db.prepare('SELECT status FROM task_runs WHERE id=?').get(taskId).status
  evidence.event_watermark = db.prepare('SELECT max(global_seq) AS seq FROM run_events').get().seq
  evidence.audit_sequence = db.prepare('SELECT max(seq) AS seq FROM audit_log').get().seq
  await page.reload()
  await page.locator('.notice').filter({ hasText: /已连接 · (idle|failed)/ }).waitFor()
  await page.screenshot({ path: `${directory}/recovered.png` })
  evidence.screenshots.push(`${directory}/recovered.png`)
  await writeFile(`${directory}/result.json`, JSON.stringify(safe(evidence), null, 2))
  process.stdout.write(JSON.stringify({ directory, task_run_id: taskId, status: evidence.final_status, actual_operations: evidence.upstream.operations.length }) + '\n')
} finally {
  if (db) {
    evidence.actual_task_states = db.prepare('SELECT id,status,current_attempt_no FROM task_runs ORDER BY created_at').all()
    evidence.actual_tool_states = db.prepare('SELECT call_id,task_run_id,tool_name,status,input_hash,dispatched_at FROM tool_calls ORDER BY prepared_at').all()
    await writeFile(`${directory}/result.json`, JSON.stringify(safe(evidence), null, 2))
  }
  await app.close()
  heldResponse?.destroy()
  await new Promise((done) => server.close(done))
  db?.close()
  upstream.close()
}
