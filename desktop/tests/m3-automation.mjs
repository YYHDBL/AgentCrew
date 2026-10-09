import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { _electron as electron } from 'playwright-core'
import { cp, mkdir, readFile, stat, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { DatabaseSync } from 'node:sqlite'

const directory = resolve('.artifacts', `m3-automation-${Date.now()}`)
await mkdir(`${directory}/data`, { recursive: true })
await cp('../backend/data/config.json', `${directory}/data/config.json`)
const configuredSecrets = Object.values(JSON.parse(await readFile(`${directory}/data/config.json`, 'utf8')).models)
  .map((entry) => entry.api_key).filter(Boolean)
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`],
  executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const page = await app.firstWindow()
page.setDefaultTimeout(60000)
let db
const result = { directory, started_at: new Date().toISOString(), model_config_source: '../backend/data/config.json', cases: [], requests: [], sql: [], screenshots: [], files: [] }
const pageErrors = []
page.on('pageerror', (error) => pageErrors.push(error.message))
page.on('request', (request) => {
  if (!request.url().includes('/api/')) return
  result.requests.push({ method: request.method(), path: new URL(request.url()).pathname + new URL(request.url()).search,
    request: request.postDataJSON() ?? null })
})
page.on('response', async (response) => {
  if (!response.url().includes('/api/')) return
  let body
  try { body = await response.json() } catch { body = { content_type: response.headers()['content-type'] ?? 'unknown' } }
  result.requests.push({ method: response.request().method(), path: new URL(response.url()).pathname + new URL(response.url()).search,
    status: response.status(), response: body })
})
const api = async (method, path, body, identity = null) => page.evaluate(async ({ method, path, body, identity }) => {
  const port = await window.agentcrew.getBackendPort()
  const token = await window.agentcrew.getToken()
  const activeIdentity = identity ?? sessionStorage.getItem('agentcrew-identity')
  const response = await fetch(`http://127.0.0.1:${port}/api${path}`, { method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...(activeIdentity ? { 'X-AgentCrew-Identity': activeIdentity } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body) })
  const text = await response.text()
  return { status: response.status, body: text ? JSON.parse(text) : {} }
}, { method, path, body, identity })
const call = async (method, path, body, identity = null) => {
  const response = await api(method, path, body, identity)
  result.requests.push({ method, path, request: body ?? null, response: response.body })
  if (response.status < 200 || response.status >= 300) throw new Error(`${method} ${path}: ${response.status} ${JSON.stringify(response.body)}`)
  return response.body.data
}
const query = (sql, values = []) => {
  result.sql.push({ query: sql, parameters: values })
  return db.prepare(sql).all(...values)
}
const sanitizeEvidence = (value) => {
  if (Array.isArray(value)) return value.map(sanitizeEvidence)
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .filter(([key]) => !/^(api_key|api_key_sha256|identity_token|access_token|authorization|bearer|token)$/i.test(key))
    .map(([key, entry]) => [key, key === 'payload' && typeof entry === 'string' ? JSON.stringify(sanitizeEvidence(JSON.parse(entry))) : sanitizeEvidence(entry)]))
  return value
}
const wait = async (read, accept, timeout = 180000) => {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const value = await read()
    if (accept(value)) return value
    await new Promise((done) => setTimeout(done, 250))
  }
  throw new Error('真实服务状态未在期限内达到验收条件')
}
const screen = async (name, width, height) => {
  await page.setViewportSize({ width, height })
  await page.screenshot({ path: `${directory}/${name}.png` })
  result.screenshots.push(`${directory}/${name}.png`)
}
const localInput = (epoch, timezone) => {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(epoch)
  const part = (name) => parts.find((item) => item.type === name).value
  const date = `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}`
  return part('second') === '00' ? date : `${date}:${part('second')}`
}
const stampFile = async (path) => {
  const info = await stat(path)
  const bytes = await readFile(path)
  return { path, sha256: createHash('sha256').update(bytes).digest('hex'), mtime: info.mtime.toISOString(), size: info.size,
    content: bytes.toString('utf8') }
}
const getWriteCandidate = async () => {
  const labels = await page.locator('.automation-preview label').all()
  for (const label of labels) {
    const text = await label.innerText()
    if (text.startsWith('write_file · ')) return { label, text, pattern: text.slice('write_file · '.length) }
  }
  return null
}
const planForm = async ({ name, kind, instruction, targetPath, every = '86400', cron = '0 0 1 1 *', at = null, mode = 'new_conversation', conversation = '' }) => {
  await page.getByRole('button', { name: '新建计划', exact: true }).click()
  await page.getByLabel('计划名称').fill(name)
  await page.getByLabel('计划类型').selectOption(kind)
  await page.getByLabel('IANA 时区').fill('Asia/Shanghai')
  if (kind === 'at') await page.getByLabel('计划时间').fill(localInput(at, 'Asia/Shanghai'))
  if (kind === 'every') await page.getByLabel('间隔秒数').fill(every)
  if (kind === 'cron') await page.getByLabel('cron 表达式').fill(cron)
  await page.getByLabel('计划员工').selectOption(result.agent.id)
  await page.getByLabel('执行会话模式').selectOption(mode)
  if (mode === 'existing') await page.getByLabel('现有会话').selectOption(conversation)
  await page.getByLabel('执行指令').fill(instruction)
  await page.getByRole('button', { name: '核查服务端候选范围与下一次时间', exact: true }).click()
  await page.locator('.automation-preview').waitFor()
  const candidate = await getWriteCandidate()
  if (candidate && targetPath && candidate.pattern !== targetPath) throw new Error('写入目标必须来自服务端候选目录')
  if (candidate && targetPath) await candidate.label.locator('input').check()
  const createButton = page.locator('.ant-modal-footer button').filter({ hasText: '创建计划' })
  assert.equal(await createButton.isEnabled(), true, '服务端预览后创建按钮仍不可用')
  await createButton.click()
  await page.getByRole('dialog', { name: '创建自动化计划', exact: true }).waitFor({ state: 'hidden' })
  const jobs = await call('GET', `/cron/jobs?workspace_id=${encodeURIComponent(result.workspace.id)}&limit=200`)
  const created = jobs.items.find((job) => job.name === name)
  assert.ok(created, `服务端没有保存计划 ${name}`)
  assert.equal(created.schedule.kind, kind)
  assert.equal(created.schedule.tz, 'Asia/Shanghai')
  return { job: created, candidate }
}
const runPlan = async (job, expectedTrigger, expectedFile) => {
  if (expectedTrigger === 'manual') await page.locator('.automation-job').filter({ hasText: job.name }).getByRole('button', { name: '手动运行', exact: true }).click()
  const occurrence = await wait(async () => {
    const rows = await call('GET', `/cron/jobs/${job.id}/runs?limit=200`)
    return rows.items.find((item) => item.trigger === expectedTrigger)
  }, Boolean)
  assert.ok(occurrence.id)
  const task = await wait(() => call('GET', `/task-runs/${occurrence.task_run_id}`), (row) => ['completed', 'failed', 'cancelled'].includes(row.status))
  if (task.status !== 'completed') throw new Error(`真实计划 TaskRun ${task.id} 以 ${task.status} 结束`)
  const attempts = await call('GET', `/task-runs/${task.id}/attempts`)
  const artifacts = await call('GET', `/conversations/${task.conversation_id}/artifacts`)
  assert.equal(attempts.length, 1)
  if (expectedFile) {
    const file = await stampFile(expectedFile)
    assert.ok(file.size > 0)
    result.files.push(file)
  }
  return { occurrence, task, attempts, artifacts }
}

let failure
try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  db = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
  await page.getByRole('button', { name: '自动化', exact: true }).click()
  await page.getByRole('heading', { name: '自动化', exact: true }).waitFor()
  await page.getByLabel('自动化工作区').waitFor()
  const spaces = await call('GET', '/workspaces?limit=200')
  const employees = await call('GET', '/agents?limit=200')
  result.workspace = spaces.items.find((row) => row.status === 'active')
  result.agent = employees.items.find((row) => row.status === 'active' && row.workspace_id === result.workspace?.id)
  assert.ok(result.workspace && result.agent)
  await page.getByLabel('自动化工作区').selectOption(result.workspace.id)
  await page.getByLabel('自动化员工').selectOption(result.agent.id)
  const workspace = await call('GET', `/workspaces/${result.workspace.id}`)
  const basePath = `${workspace.data_dir}/files`
  result.data_dir = workspace.data_dir
  const sourceDb = new DatabaseSync(resolve('../data/m3-intermediate/02-api-acceptance/identity-http0/agentcrew.db'), { readOnly: true })
  const sourceSoul = sourceDb.prepare("SELECT id,model,task_run_id,result FROM memory_soul_generations WHERE status='completed' ORDER BY created_at LIMIT 1").get()
  sourceDb.close()
  assert.ok(sourceSoul?.result, '缺少已由真实模型生成的岗位 soul 来源')
  result.soul_source = { id: sourceSoul.id, model: sourceSoul.model, task_run_id: sourceSoul.task_run_id, characters: sourceSoul.result.length,
    sha256: createHash('sha256').update(sourceSoul.result).digest('hex') }
  await call('POST', `/memory/stores/soul/${result.agent.id}?workspace_id=${encodeURIComponent(result.workspace.id)}&agent_id=${encodeURIComponent(result.agent.id)}`,
    { change_id: randomUUID(), expected_revision: 0, basis: `M3-13使用历史真实生成 ${sourceSoul.id}（${sourceSoul.model}，TaskRun ${sourceSoul.task_run_id}）初始化独立验收员工。`, text: sourceSoul.result })
  result.permission_rule = await call('POST', `/agents/${result.agent.id}/permission-rules`, {
    change_id: randomUUID(), tool_name: 'write_file', pattern: basePath, effect: 'allow' })
  await screen('automation-wide', 1440, 900)

  const atFile = `${basePath}/m3-13-at-${randomUUID()}.txt`
  const atName = `M3-13 at ${randomUUID()}`
  const atPlan = await planForm({ name: atName, kind: 'at', at: Date.now() + 12000, targetPath: basePath,
    instruction: `只使用write_file在${atFile}写入“AgentCrew M3-13 at actual file”，随后使用read_file实际核对正文。不得调用其他工具。` })
  assert.equal(atPlan.job.schedule.at_ms > Date.now(), true)
  assert.equal(atPlan.job.target.execution_mode, 'new_conversation')
  result.at = atPlan.job
  const atExecution = await runPlan(atPlan.job, 'scheduled', atFile)
  result.at_execution = atExecution
  result.cases.push('界面创建 at 计划，按选定时区保存参数并通过调度产生真实 TaskRun 和文件')

  const everyFile = `${basePath}/m3-13-every-${randomUUID()}.txt`
  const everyName = `M3-13 every ${randomUUID()}`
  const everyPlan = await planForm({ name: everyName, kind: 'every', every: '1', targetPath: basePath,
    instruction: `只使用write_file在${everyFile}写入“AgentCrew M3-13 every actual file”，随后使用read_file实际核对正文。不得调用其他工具。` })
  assert.equal(everyPlan.job.schedule.every_ms, 1000)
  result.every = everyPlan.job
  const firstOccurrence = await wait(async () => {
    const rows = await call('GET', `/cron/jobs/${everyPlan.job.id}/runs?limit=200`)
    return rows.items.find((item) => item.trigger === 'scheduled' && item.status === 'fired')
  }, Boolean)
  const everyTaskStarted = await wait(() => call('GET', `/task-runs/${firstOccurrence.task_run_id}`), (row) => ['running', 'completed', 'failed', 'cancelled'].includes(row.status))
  assert.equal(everyTaskStarted.status, 'running', JSON.stringify(everyTaskStarted))
  const duplicate = await call('POST', `/cron/jobs/${everyPlan.job.id}/run-now`, { client_request_id: randomUUID(), expected_revision: everyPlan.job.revision })
  assert.equal(duplicate.status, 'skipped')
  result.same_plan_conflict = duplicate
  await page.locator('.automation-job').filter({ hasText: everyName }).getByRole('button', { name: '停用', exact: true }).click()
  result.every_disabled = await wait(() => call('GET', `/cron/jobs/${everyPlan.job.id}`), (row) => !row.state.enabled)
  const disabledHistory = await call('GET', `/cron/jobs/${everyPlan.job.id}/runs?limit=200`)
  await new Promise((done) => setTimeout(done, 2000))
  const afterDisableHistory = await call('GET', `/cron/jobs/${everyPlan.job.id}/runs?limit=200`)
  assert.deepEqual(afterDisableHistory.items.map((item) => item.id).sort(), disabledHistory.items.map((item) => item.id).sort())
  const everyTask = await wait(() => call('GET', `/task-runs/${firstOccurrence.task_run_id}`), (row) => ['completed', 'failed', 'cancelled'].includes(row.status))
  if (everyTask.status === 'failed') {
    assert.equal(everyTask.error, 'PLAN_DISABLED：计划已停用或修订已经改变')
    await assert.rejects(readFile(everyFile))
  } else {
    assert.equal(everyTask.status, 'completed', JSON.stringify(everyTask))
    result.files.push(await stampFile(everyFile))
  }
  const everyAttempts = await call('GET', `/task-runs/${everyTask.id}/attempts`)
  const everyArtifacts = await call('GET', `/conversations/${everyTask.conversation_id}/artifacts`)
  assert.ok(everyAttempts.length <= 1)
  result.every_execution = { occurrence: firstOccurrence, task: everyTask, attempts: everyAttempts, artifacts: everyArtifacts }
  result.cases.push(`界面创建 every 计划并触发真实定时任务；执行期间的手动冲突记录 skipped，随后停用计划后当前任务实际结果为 ${everyTask.status}${everyTask.error ? `（${everyTask.error}）` : ''}`)

  const cronFile = `${basePath}/m3-13-cron-${randomUUID()}.txt`
  const cronName = `M3-13 cron ${randomUUID()}`
  const cronPlan = await planForm({ name: cronName, kind: 'cron', cron: '0 0 1 1 *', targetPath: basePath,
    instruction: `只使用write_file在${cronFile}写入“AgentCrew M3-13 cron actual file”，随后使用read_file实际核对正文。不得调用其他工具。` })
  assert.equal(cronPlan.job.schedule.expr, '0 0 1 1 *')
  result.cron = cronPlan.job
  result.cron_execution = await runPlan(cronPlan.job, 'manual', cronFile)
  await page.locator('.automation-job').filter({ hasText: cronName }).getByRole('button', { name: '编辑', exact: true }).click()
  await page.getByLabel('计划名称').fill(`${cronName} edited`)
  await page.getByLabel('cron 表达式').fill('0 1 1 1 *')
  await page.getByRole('button', { name: '核查服务端候选范围与下一次时间', exact: true }).click()
  await page.locator('.automation-preview').waitFor()
  const saveButton = page.locator('.ant-modal-footer button').filter({ hasText: '保存修改' })
  assert.equal(await saveButton.isEnabled(), true, '服务端预览后保存按钮仍不可用')
  await saveButton.click()
  await page.getByRole('dialog', { name: '编辑自动化计划', exact: true }).waitFor({ state: 'hidden' })
  const edited = await call('GET', `/cron/jobs/${cronPlan.job.id}`)
  assert.equal(edited.schedule.expr, '0 1 1 1 *')
  assert.equal(edited.revision, cronPlan.job.revision + 1)
  result.cron_edited = edited
  await page.locator('.automation-job').filter({ hasText: `${cronName} edited` }).getByRole('button', { name: '停用', exact: true }).click()
  const disabled = await wait(() => call('GET', `/cron/jobs/${cronPlan.job.id}`), (row) => !row.state.enabled)
  result.cron_disabled = disabled
  await page.locator('.automation-job').filter({ hasText: `${cronName} edited` }).getByRole('button', { name: '删除', exact: true }).click()
  await page.getByRole('dialog', { name: '删除计划' }).getByRole('button', { name: '确认删除', exact: true }).click()
  const deleted = await wait(() => call('GET', `/cron/jobs/${cronPlan.job.id}`), (row) => row.deleted_at !== null)
  result.cron_deleted = deleted
  result.cases.push('界面创建 cron 计划、手动运行、修改、停用和删除；历史 TaskRun 与产物查询保持可用')

  const existingName = `M3-13 existing target ${randomUUID()}`
  const existingPlan = await planForm({ name: existingName, kind: 'every', every: '86400', mode: 'existing',
    conversation: atExecution.task.conversation_id, instruction: '只返回收到，不调用工具。', targetPath: null })
  assert.equal(existingPlan.job.target.execution_mode, 'existing')
  assert.equal(existingPlan.job.target.conversation_id, atExecution.task.conversation_id)
  result.existing_plan = existingPlan.job
  await page.locator('.automation-job').filter({ hasText: existingName }).getByRole('button', { name: '编辑', exact: true }).click()
  await page.getByLabel('执行会话模式').selectOption('new_conversation')
  await page.getByLabel('执行指令').fill('编辑为新会话计划，仅回复收到，不调用工具。')
  await page.getByRole('button', { name: '核查服务端候选范围与下一次时间', exact: true }).click()
  await page.locator('.automation-preview').waitFor()
  const existingEditSave = page.locator('.ant-modal-footer button').filter({ hasText: '保存修改' })
  assert.equal(await existingEditSave.isEnabled(), true)
  await existingEditSave.click()
  await page.getByRole('dialog', { name: '编辑自动化计划', exact: true }).waitFor({ state: 'hidden' })
  const editedTarget = await call('GET', `/cron/jobs/${existingPlan.job.id}`)
  assert.equal(editedTarget.target.execution_mode, 'new_conversation')
  assert.equal(editedTarget.target.conversation_id, null)
  result.existing_plan_edited = editedTarget
  await page.locator('.automation-job').filter({ hasText: `${existingName}` }).getByRole('button', { name: '删除', exact: true }).click()
  await page.getByRole('dialog', { name: '删除计划' }).getByRole('button', { name: '确认删除', exact: true }).click()
  await wait(() => call('GET', `/cron/jobs/${existingPlan.job.id}`), (row) => row.deleted_at !== null)
  result.cases.push('界面创建并编辑现有会话目标为新建会话，服务端持久化会话模式和目标变化')

  const missingFile = `${basePath}/m3-13-denied-${randomUUID()}.txt`
  const deniedPlan = await planForm({ name: `M3-13 no preauthorization ${randomUUID()}`, kind: 'every', targetPath: null,
    instruction: `只尝试使用write_file在${missingFile}写入“must not be written”。不得调用其他工具。` })
  const deniedOccurrence = await call('POST', `/cron/jobs/${deniedPlan.job.id}/run-now`, { client_request_id: randomUUID(), expected_revision: deniedPlan.job.revision })
  const deniedTask = await wait(() => call('GET', `/task-runs/${deniedOccurrence.task_run_id}`), (row) => ['completed', 'failed', 'cancelled'].includes(row.status))
  const deniedEvents = await call('GET', `/task-runs/${deniedTask.id}/events?limit=200`)
  const deniedTools = deniedEvents.items.filter((item) => item.type === 'tool.failed' || item.type === 'tool.pending_verification')
  assert.ok(deniedTools.some((item) => JSON.stringify(item.payload).includes('PRE_AUTH_EXCEEDED')))
  await assert.rejects(readFile(missingFile))
  result.denied = { job: deniedPlan.job, occurrence: deniedOccurrence, task: deniedTask, events: deniedTools, file_path: missingFile }
  await page.locator('.automation-job').filter({ hasText: deniedPlan.job.name }).getByRole('button', { name: '停用', exact: true }).click()
  result.cases.push('真实无人值守预授权拒绝写入，保存拒绝事件且目标文件未生成')

  const missedName = `M3-13 missed ${randomUUID()}`
  const missedPlan = await planForm({ name: missedName, kind: 'at', at: Date.now() + 10000,
    instruction: '只返回收到，不调用工具。' })
  await wait(async () => Date.now(), (now) => now >= missedPlan.job.state.next_run_at - 1000, 15000)
  const processLine = execFileSync('ps', ['-axo', 'pid,command'], { encoding: 'utf8' }).split('\n')
    .find((line) => line.includes('/python3 -m agentcrew_server') && line.includes(directory))
  assert.ok(processLine, '没有找到本次真实 Electron sidecar')
  const sidecarPid = Number(processLine.trim().split(/\s+/)[0])
  const stoppedAt = new Date().toISOString()
  process.kill(sidecarPid, 'SIGSTOP')
  await new Promise((done) => setTimeout(done, 4000))
  const continuedAt = new Date().toISOString()
  process.kill(sidecarPid, 'SIGCONT')
  result.signal = { pid: sidecarPid, signal: 'SIGSTOP/SIGCONT', stopped_at: stoppedAt, continued_at: continuedAt }
  const missedOccurrence = await wait(() => query('SELECT * FROM cron_job_runs WHERE job_id=? ORDER BY triggered_at DESC', [missedPlan.job.id]), (rows) => rows.some((row) => row.status === 'missed'))
  result.missed = missedOccurrence
  await page.getByRole('button', { name: '刷新实际记录', exact: true }).click()
  const missedCard = page.locator('.automation-job').filter({ hasText: missedName })
  await wait(async () => missedCard.innerText(), (text) => text.includes('最近状态 已错过'))
  result.cases.push('真实 SIGSTOP/SIGCONT 越过 at 到期时间，SQL 与刷新页面均显示 missed 且没有 TaskRun')

  const taskName = `M3-13 actual proposal ${randomUUID()}`
  const proposalFile = `${basePath}/m3-13-proposal-${randomUUID()}.txt`
  const prompt = `必须仅调用一次schedule_task，精确参数如下：${JSON.stringify({ name: taskName,
    schedule: { kind: 'every', every_ms: 86400000, tz: 'Asia/Shanghai' },
    instruction: `只使用write_file在${proposalFile}写入“approved actual proposal file”，随后使用read_file核查该文件。不得调用其他工具。`,
    execution_mode: 'new_conversation', pre_authorized: [{ tool: 'write_file', pattern: basePath }] })}。该次只提出计划，等待真人决定。不得自己写入文件。`
  const source = await call('POST', '/conversations', { workspace_id: result.workspace.id, agent_id: result.agent.id, client_request_id: randomUUID(), instruction: prompt })
  result.proposal_source = source
  const proposalRow = await wait(() => query('SELECT * FROM cron_proposals WHERE task_run_id=? ORDER BY created_at DESC', [source.task_run_id]), (rows) => rows.length > 0)
  const proposalId = proposalRow[0].id
  const proposalBefore = await call('GET', `/cron/proposals/${proposalId}`)
  result.proposal_before = proposalBefore
  const preapprovalJobs = query("SELECT id FROM cron_jobs WHERE json_extract(metadata,'$.proposal_id')=?", [proposalId])
  assert.equal(preapprovalJobs.length, 0)
  await page.getByRole('heading', { name: taskName, exact: true }).waitFor()
  await screen('automation-approval-wide', 1440, 900)
  const candidate = proposalBefore.candidates.find((item) => item.tool === 'write_file' && item.pattern === basePath)
  assert.ok(candidate, '真实提案候选不包含服务端提供的写入目录')
  await page.locator('.automation-proposal').filter({ hasText: taskName }).getByLabel(`${candidate.tool} ${candidate.pattern}`).check().catch(async () => {
    const matching = page.locator('.automation-proposal .automation-choice').filter({ hasText: candidate.pattern })
    await matching.locator('input').check()
  })
  const taskState = await call('GET', `/task-runs/${source.task_run_id}`)
  result.proposal_task_state_before = taskState
  const approvalCard = page.locator('.automation-proposal').filter({ hasText: taskName })
  await approvalCard.getByRole('button', { name: '批准并创建唯一计划', exact: true }).click()
  const approved = await wait(() => call('GET', `/cron/proposals/${proposalId}`), (row) => row.status === 'approved')
  assert.ok(approved.job_id)
  const approvedPlanRows = query("SELECT * FROM cron_jobs WHERE json_extract(metadata,'$.proposal_id')=?", [proposalId])
  assert.equal(approvedPlanRows.length, 1)
  const proposalDecision = { decision: 'allow_once', input_hash: proposalBefore.input_hash, expected_revision: proposalBefore.revision, selected: approved.selected }
  const replayApproval = await call('POST', `/cron/proposals/${proposalId}`, proposalDecision)
  assert.equal(replayApproval.job_id, approved.job_id)
  assert.equal(query("SELECT id FROM cron_jobs WHERE json_extract(metadata,'$.proposal_id')=?", [proposalId]).length, 1)
  result.approved = { proposal: approved, plan_rows: approvedPlanRows, duplicate_decision: replayApproval, parameters: proposalDecision }
  result.cases.push('界面依据持久原提案批准真实员工请求；批准前无计划、批准后唯一计划、重复批准复用同一计划')

  const proposalTask = await call('GET', `/task-runs/${source.task_run_id}`)
  if (['running', 'waiting_approval'].includes(proposalTask.status)) {
    const questions = await call('GET', `/conversations/${proposalTask.conversation_id}/questions`)
    for (const question of questions) await call('POST', `/questions/${question.request_id}/answer`, { answer: '请按真实提案状态结束，不执行额外操作。' })
  }
  const postApprovalPlan = await call('GET', `/cron/jobs/${approved.job_id}`)
  const proposalExecution = await runPlan(postApprovalPlan, 'manual', proposalFile)
  result.proposal_execution = proposalExecution
  const proposalOrigin = query("SELECT id,metadata,json_extract(metadata,'$.created_by') AS created_by,json_extract(metadata,'$.created_via_task_run_id') AS created_via_task_run_id FROM cron_jobs WHERE id=?", [approved.job_id])
  assert.equal(proposalOrigin[0].created_by, 'agent')
  assert.equal(proposalOrigin[0].created_via_task_run_id, source.task_run_id)
  result.cases.push('经界面批准的员工计划真实执行并产生文件产物，created_by 与来源 TaskRun 保持关联')

  const proposalInstructionReject = `必须仅调用一次schedule_task，精确参数如下：${JSON.stringify({ name: `M3-13 rejected ${randomUUID()}`,
    schedule: { kind: 'every', every_ms: 86400000, tz: 'Asia/Shanghai' }, instruction: '只返回收到，不调用工具。', execution_mode: 'new_conversation', pre_authorized: [] })}。只提交提案，等待真人决定。`
  const rejectedSource = await call('POST', '/conversations', { workspace_id: result.workspace.id, agent_id: result.agent.id, client_request_id: randomUUID(), instruction: proposalInstructionReject })
  const rejectedRows = await wait(() => query('SELECT * FROM cron_proposals WHERE task_run_id=?', [rejectedSource.task_run_id]), (rows) => rows.length > 0)
  const rejectedBefore = await call('GET', `/cron/proposals/${rejectedRows[0].id}`)
  await page.getByRole('button', { name: '刷新实际记录', exact: true }).click()
  const rejectedCard = page.locator('.automation-proposal').filter({ hasText: rejectedBefore.proposal.name })
  let rejectionState = { proposal: rejectedBefore, cardCount: 0 }
  if (rejectedBefore.status === 'pending') rejectionState = await wait(async () => ({
    proposal: await call('GET', `/cron/proposals/${rejectedBefore.id}`), cardCount: await rejectedCard.count()
  }), (state) => state.proposal.status !== 'pending' || state.cardCount > 0, 30000)
  if (rejectionState.proposal.status === 'pending') {
    await rejectedCard.getByRole('button', { name: '拒绝提案', exact: true }).click()
  }
  const rejected = await wait(() => call('GET', `/cron/proposals/${rejectedBefore.id}`), (row) => row.status === 'rejected' || row.status === 'expired')
  assert.equal(query("SELECT id FROM cron_jobs WHERE json_extract(metadata,'$.proposal_id')=?", [rejected.id]).length, 0)
  result.rejected = rejected
  if (rejected.status === 'expired') {
    const stale = await api('POST', `/cron/proposals/${rejected.id}`, { decision: 'allow_once', input_hash: rejected.input_hash, expected_revision: rejectedBefore.revision, selected: [] })
    assert.equal(stale.status, 409)
    assert.equal(query("SELECT id FROM cron_jobs WHERE json_extract(metadata,'$.proposal_id')=?", [rejected.id]).length, 0)
    result.expired_decision = stale
  }
  result.cases.push(rejected.status === 'rejected' ? '界面拒绝真实提案，服务端未创建计划' : '真实服务端使提案过期，旧决定返回409且未创建计划')

  const expiringName = `M3-13 expired ${randomUUID()}`
  const expiringPrompt = `必须仅调用一次schedule_task，精确参数如下：${JSON.stringify({ name: expiringName,
    schedule: { kind: 'every', every_ms: 86400000, tz: 'Asia/Shanghai' }, instruction: '只返回收到，不调用工具。', execution_mode: 'new_conversation', pre_authorized: [] })}。只提交提案，等待真人决定。`
  const expiringSource = await call('POST', '/conversations', { workspace_id: result.workspace.id, agent_id: result.agent.id, client_request_id: randomUUID(), instruction: expiringPrompt })
  const expiringRows = await wait(() => query('SELECT * FROM cron_proposals WHERE task_run_id=?', [expiringSource.task_run_id]), (rows) => rows.length > 0)
  const expiringBefore = await call('GET', `/cron/proposals/${expiringRows[0].id}`)
  const memberIdentity = await call('POST', '/identity/demo', { user_id: 'lilei', change_id: randomUUID() })
  const memberRecord = await api('GET', '/identity', undefined, memberIdentity.identity_token)
  assert.equal(memberRecord.body.data.role, 'member')
  const memberDecision = await api('POST', `/cron/proposals/${expiringBefore.id}`, {
    decision: 'allow_once', input_hash: expiringBefore.input_hash, expected_revision: expiringBefore.revision, selected: []
  }, memberIdentity.identity_token)
  assert.equal(memberDecision.status, 403)
  result.member_decision = { role: memberRecord.body.data.role, status: memberDecision.status, error: memberDecision.body.error }
  await call('POST', `/task-runs/${expiringSource.task_run_id}/cancel`)
  const expired = await wait(() => call('GET', `/cron/proposals/${expiringBefore.id}`), (row) => row.status === 'expired')
  const oldApproval = await api('POST', `/cron/proposals/${expired.id}`, {
    decision: 'allow_once', input_hash: expired.input_hash, expected_revision: expiringBefore.revision, selected: []
  })
  assert.equal(oldApproval.status, 409)
  assert.equal(query("SELECT id FROM cron_jobs WHERE json_extract(metadata,'$.proposal_id')=?", [expired.id]).length, 0)
  result.expired = { proposal: expired, stale_decision: { status: oldApproval.status, error: oldApproval.body.error } }
  result.cases.push('真实成员身份审批返回403；取消真实待审批任务后提案过期，旧决定返回409且没有计划')

  await page.getByRole('button', { name: '工作台', exact: true }).click()
  await page.getByRole('button', { name: '自动化', exact: true }).click()
  await page.getByRole('heading', { name: '自动化', exact: true }).waitFor()
  await page.getByLabel('演示身份').selectOption('lilei')
  const memberView = await wait(() => call('GET', '/identity'), (identity) => identity.effective_user_id === 'lilei')
  assert.equal(memberView.role, 'member')
  await page.getByRole('button', { name: '自动化', exact: true }).click()
  await page.getByRole('heading', { name: '自动化', exact: true }).waitFor()
  assert.equal(await page.locator('.automation-job').filter({ hasText: atName }).count(), 0)
  assert.equal(await page.locator('.automation-proposal').filter({ hasText: taskName }).count(), 0)
  result.identity_scope = { effective_user_id: memberView.effective_user_id, role: memberView.role, owner_plan_hidden: true, owner_proposal_hidden: true }
  await page.getByLabel('演示身份').selectOption('owner')
  await wait(() => call('GET', '/identity'), (identity) => identity.effective_user_id === 'owner')
  await page.getByRole('button', { name: '自动化', exact: true }).click()
  await page.getByRole('heading', { name: '自动化', exact: true }).waitFor()
  const newPlanButton = page.getByRole('button', { name: '新建计划', exact: true })
  await newPlanButton.focus()
  await newPlanButton.click()
  await page.getByRole('dialog', { name: '创建自动化计划', exact: true }).waitFor()
  await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '计划名称')
  await page.getByLabel('计划名称').focus()
  await page.keyboard.press('Escape')
  await page.getByRole('dialog', { name: '创建自动化计划', exact: true }).waitFor({ state: 'hidden' })
  await page.waitForFunction(() => document.activeElement?.textContent?.trim() === '新建计划')
  assert.equal(pageErrors.length, 0, pageErrors.join('\n'))
  result.cases.push('身份切换清除旧员工的计划和提案；回到真人身份后刷新；宽窄窗口与键盘弹窗检查')
  await screen('automation-narrow', 960, 900)
  result.current_identity = await call('GET', '/identity')
  result.audit_verification = await call('POST', '/audit/verify', {})
  const chain = await readFile(`${directory}/data/chain-head.txt`, 'utf8')
  result.audit_chain_head = chain.trim()

  const ids = [atPlan.job.id, everyPlan.job.id, cronPlan.job.id, existingPlan.job.id, deniedPlan.job.id, missedPlan.job.id, approved.job_id, rejected.id]
  const placeholders = ids.map(() => '?').join(',')
  result.database = {
    jobs: query(`SELECT * FROM cron_jobs WHERE id IN (${placeholders}) ORDER BY created_at,id`, ids),
    proposals: query('SELECT * FROM cron_proposals WHERE id IN (?,?) ORDER BY created_at,id', [proposalId, rejected.id]),
    occurrences: query(`SELECT * FROM cron_job_runs WHERE job_id IN (${placeholders}) ORDER BY triggered_at,id`, ids),
    cron_attempts: query(`SELECT a.* FROM cron_run_attempts a JOIN cron_job_runs r ON r.id=a.occurrence_id WHERE r.job_id IN (${placeholders}) ORDER BY r.job_id,a.retry_no,a.attempt_no`, ids),
    task_runs: query(`SELECT t.* FROM task_runs t WHERE t.cron_job_id IN (${placeholders}) ORDER BY t.created_at,t.id`, ids),
    attempts: query(`SELECT a.* FROM run_attempts a JOIN task_runs t ON t.id=a.task_run_id WHERE t.cron_job_id IN (${placeholders}) ORDER BY t.cron_job_id,a.attempt_no`, ids),
    events: query(`SELECT e.global_seq,e.seq,e.task_run_id,e.type,e.created_at,e.payload FROM run_events e WHERE e.global_seq>(SELECT coalesce(min(global_seq),0) FROM run_events WHERE type LIKE 'cron.%') ORDER BY e.global_seq`, []),
    audit: query(`SELECT * FROM audit_log WHERE resource_type IN ('cron_job','cron_proposal') ORDER BY seq`, [])
  }
  result.event_watermarks = result.database.events.map((event) => ({ global_seq: event.global_seq, seq: event.seq, task_run_id: event.task_run_id, type: event.type }))
  result.audit_chain = { checked_rows: result.audit_verification.internal.checked_rows, internal: result.audit_verification.internal, anchor: result.audit_verification.anchor, chain_head: result.audit_chain_head }
  result.ended_at = new Date().toISOString()
} catch (error) {
  failure = error
  result.failure = { message: error instanceof Error ? error.message : String(error), at: new Date().toISOString() }
  try { result.interface_on_failure = { dialogs: await page.locator('[role="dialog"]').allInnerTexts(), buttons: await page.locator('button').allTextContents() } } catch {}
  if (result.proposal_source?.task_run_id) {
    try { result.failed_proposal_task = { task: query('SELECT * FROM task_runs WHERE id=?', [result.proposal_source.task_run_id]), proposals: query('SELECT * FROM cron_proposals WHERE task_run_id=?', [result.proposal_source.task_run_id]), calls: query('SELECT l.* FROM llm_calls l JOIN steps s ON s.id=l.step_id WHERE s.task_run_id=? ORDER BY s.ordinal,l.id', [result.proposal_source.task_run_id]), events: query('SELECT seq,global_seq,type,payload FROM run_events WHERE task_run_id=? ORDER BY seq', [result.proposal_source.task_run_id]) } } catch {}
  }
  try { result.identity_on_failure = await call('GET', '/identity') } catch {}
} finally {
  result.page_errors = pageErrors
  result.ended_at ??= new Date().toISOString()
  const credentialMatches = configuredSecrets.filter((secret) => JSON.stringify(sanitizeEvidence(result)).includes(secret)).length
  result.evidence_scan = { actual_config_secret_matches: credentialMatches }
  if (credentialMatches) result.failure = { message: '脱敏证据中发现实际配置凭据', at: new Date().toISOString() }
  const safeResult = sanitizeEvidence(result)
  const safeText = JSON.stringify(safeResult, null, 2)
  await writeFile(`${directory}/acceptance-result.json`, safeText)
  db.close()
  await app.close()
  if (credentialMatches) failure = new Error('脱敏证据中发现实际配置凭据')
}
console.log(JSON.stringify({ directory, cases: result.cases, screenshots: result.screenshots, failure: result.failure ?? null }, null, 2))
if (failure) throw failure
