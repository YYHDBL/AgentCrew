import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { cp, mkdir, readFile, stat, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { _electron as electron } from 'playwright-core'
import { openSettings, changeIdentity, closeDesktop, recordDesktopEvidence } from './helpers/desktop.mjs'

const directory = resolve('.artifacts', `m3-acceptance-${Date.now()}`)
await mkdir(`${directory}/data`, { recursive: true })
await cp('../backend/data/config.json', `${directory}/data/config.json`)
const preparedFiles = `${directory}/data/workspaces/office/files`
const generated = JSON.parse(execFileSync(resolve('../backend/.venv/bin/python'), [resolve('../scripts/seed/m3_automation_data.py'), '--output-dir', `${preparedFiles}/m3-materials`], { encoding: 'utf8' }))
const materials = JSON.parse(await readFile(generated.manifest, 'utf8'))
const config = JSON.parse(await readFile(`${directory}/data/config.json`, 'utf8'))
const secrets = new Set(Object.values(config.models).map((model) => model.api_key).filter(Boolean))
const redact = (value) => {
  if (Array.isArray(value)) return value.map(redact)
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .filter(([key]) => !/^(api_key|identity_token|access_token|authorization|token|thinking_blocks)$/i.test(key))
    .map(([key, item]) => [key, redact(item)]))
  if (typeof value === 'string') {
    for (const secret of secrets) value = value.replaceAll(secret, '[已脱敏]')
  }
  return value
}
const names = ['at/every/cron 真实触发', '错过不补跑与冲突不积压', '既有会话与新建会话', '无人值守预授权边界', '失败审查与原事件定位', '报告固化为技能并复用', '员工创建必经真人批准']
const result = { directory, started_at: new Date().toISOString(), materials, checks: names.map((name, index) => ({ item: index + 1, name, status: '未验证', evidence: {} })), http: [], sql: [], files: [], screens: [], platform: { notification_click: '未验证', actual_suspend_resume: '未验证' }, errors: [] }
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`], executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const child = app.process()
const page = await app.firstWindow()
page.setDefaultTimeout(90000)
page.on('pageerror', (error) => result.errors.push(error.message))
let database
let pausedPid = null
let completed = false
const query = (sql, parameters = []) => {
  const rows = database.prepare(sql).all(...parameters)
  result.sql.push({ sql, parameters, rows: redact(rows) })
  return rows
}
const request = async (method, path, body) => {
  const response = await page.evaluate(async ({ method, path, body }) => {
    const port = await window.agentcrew.getBackendPort()
    const token = await window.agentcrew.getToken()
    if (!port || !token) throw new Error('任务服务尚未就绪')
    const identity = sessionStorage.getItem('agentcrew-identity')
    const response = await fetch(`http://127.0.0.1:${port}/api${path}`, { method,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...(identity ? { 'X-AgentCrew-Identity': identity } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(60000) })
    const text = await response.text()
    return { status: response.status, body: text ? JSON.parse(text) : null }
  }, { method, path, body })
  if (response.body?.data?.identity_token) secrets.add(response.body.data.identity_token)
  result.http.push({ at: new Date().toISOString(), method, path, request: redact(body ?? null), response: redact(response) })
  return response
}
const call = async (method, path, body) => {
  const response = await request(method, path, body)
  if (response.status < 200 || response.status >= 300) throw new Error(`${method} ${path}: ${response.status} ${JSON.stringify(redact(response.body))}`)
  return response.body?.data
}
const wait = async (read, accepts, timeout = 240000) => {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const actual = await read()
    if (accepts(actual)) return actual
    await new Promise((done) => setTimeout(done, 200))
  }
  throw new Error('真实 M3 流程未在期限内达到验收条件')
}
const mark = (item, evidence) => { result.checks[item - 1].status = '通过'; result.checks[item - 1].evidence = redact(evidence) }
const record = async (name) => {
  const path = `${directory}/${name}.json`
  await recordDesktopEvidence(page, path)
  result.screens.push(path)
}
const file = async (path) => {
  const bytes = await readFile(path)
  const metadata = await stat(path)
  const value = { path, sha256: createHash('sha256').update(bytes).digest('hex'), size: bytes.length, mtime: metadata.mtime.toISOString(), text: bytes.toString('utf8') }
  result.files.push(value)
  return value
}
const localInput = (epoch) => {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(epoch)
  const part = (name) => parts.find((item) => item.type === name).value
  return `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}:${part('second')}`
}
const automation = async () => {
  await page.getByRole('button', { name: '自动化', exact: true }).click()
  await page.getByLabel('自动化工作区').selectOption('office')
  await wait(() => page.getByLabel('自动化员工').inputValue(), (id) => id === 'xiaowen')
}
const createPlan = async ({ name, kind = 'every', path, text = 'M3 实际文件', authorized = true, everySeconds = 1, at = Date.now() + 15000 }) => {
  await page.getByRole('button', { name: '新建计划', exact: true }).click()
  await page.getByLabel('计划名称').fill(name)
  await page.getByLabel('计划类型').selectOption(kind)
  await page.getByLabel('IANA 时区').fill('Asia/Shanghai')
  if (kind === 'at') await page.getByLabel('计划时间').fill(localInput(at))
  if (kind === 'every') await page.getByLabel('间隔秒数').fill(String(everySeconds))
  if (kind === 'cron') await page.getByLabel('cron 表达式').fill('* * * * *')
  await page.getByLabel('计划员工').selectOption('xiaowen')
  await page.getByLabel('执行会话模式').selectOption('new_conversation')
  await page.getByLabel('执行指令').fill(`首次工具调用只执行 write_file，在 ${path} 写入“${text}”。写入成功后才调用 read_file 核对。只写一次，仅使用这两个工具，核对后直接结束。`)
  await page.getByRole('button', { name: '核查服务端候选范围与下一次时间', exact: true }).click()
  await page.locator('.automation-preview').waitFor()
  if (authorized) {
    const candidate = page.locator('.automation-preview label').filter({ hasText: `write_file · ${preparedFiles}` })
    await candidate.locator('input').check()
  }
  await page.locator('.ant-modal-footer button').filter({ hasText: '创建计划' }).click()
  await page.getByRole('dialog', { name: '创建自动化计划', exact: true }).waitFor({ state: 'hidden' })
  return (await call('GET', '/cron/jobs?workspace_id=office&limit=200')).items.find((job) => job.name === name)
}
const disable = async (job) => {
  const actual = await call('GET', `/cron/jobs/${job.id}`)
  return call('PATCH', `/cron/jobs/${job.id}`, { change_id: randomUUID(), expected_revision: actual.revision, enabled: false })
}
const finish = async (taskId) => {
  const task = await wait(() => call('GET', `/task-runs/${taskId}`), (task) => ['completed', 'failed', 'cancelled', 'interrupted', 'waiting_verification'].includes(task.status))
  assert.equal(task.status, 'completed', JSON.stringify(redact(task)))
  return task
}

try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  database = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
  const workspace = await call('GET', '/workspaces/office')
  assert.equal(workspace.data_dir, preparedFiles)
  const source = new DatabaseSync(resolve('../data/m3-intermediate/02-api-acceptance/identity-http0/agentcrew.db'), { readOnly: true })
  const soul = source.prepare("SELECT id,model,task_run_id,result FROM memory_soul_generations WHERE agent_id='xiaowen' AND status='completed' ORDER BY created_at LIMIT 1").get()
  source.close()
  assert.ok(soul?.result, '需要真实模型生成的员工岗位记忆来源')
  await call('POST', '/memory/stores/soul/xiaowen?workspace_id=office&agent_id=xiaowen', { change_id: randomUUID(), expected_revision: 0, text: soul.result,
    basis: `M3 真实贯穿使用生成 ${soul.id}、模型 ${soul.model}、来源 TaskRun ${soul.task_run_id}` })
  result.soul_source = { id: soul.id, model: soul.model, task_run_id: soul.task_run_id, sha256: createHash('sha256').update(soul.result).digest('hex') }
  result.permission_rule = await call('POST', '/agents/xiaowen/permission-rules', {
    change_id: randomUUID(), tool_name: 'write_file', pattern: preparedFiles, effect: 'allow' })
  await page.getByLabel('工作区', { exact: true }).selectOption('office')
  await page.getByLabel('数字员工', { exact: true }).selectOption('xiaowen')
  const instruction = `读取 ${materials.inputs.orders} 与 ${materials.inputs.context}，计算订单数量、商品数量、总金额以及各类别金额，将结果写入 ${materials.outputs.summary} 的纯 JSON 正文，不使用代码围栏。顶层字段为 order_count、total_quantity、total_amount、categories；每个分类包含 category、order_count、quantity、amount。金额保留两位小数，保留全部订单，不修改输入；仅调用 read_file、write_file。写入后再实际读取核对。`
  await page.getByRole('textbox', { name: '任务指令', exact: true }).fill(instruction)
  await page.getByRole('button', { name: '发送任务', exact: true }).click()
  const conversationId = await wait(() => page.evaluate(() => sessionStorage.getItem('conversation')), Boolean)
  const initialRuns = await wait(() => call('GET', `/conversations/${conversationId}/task-runs`), (rows) => rows.length > 0)
  const initialTask = await finish(initialRuns[0].id)
  const summaryFile = await file(materials.outputs.summary)
  const summary = JSON.parse(summaryFile.text)
  assert.equal(Number(summary.order_count), materials.expected.order_count)
  assert.equal(Number(summary.total_quantity), materials.expected.total_quantity)
  assert.equal(Number(summary.total_amount), Number(materials.expected.total_amount))
  assert.deepEqual(summary.categories.map((entry) => ({ category: entry.category, order_count: Number(entry.order_count), quantity: Number(entry.quantity), amount: Number(entry.amount).toFixed(2) })).sort((left, right) => left.category.localeCompare(right.category)),
    materials.expected.categories.toSorted((left, right) => left.category.localeCompare(right.category)))
  await record('workbench')

  await automation()
  const timed = []
  for (const kind of ['at', 'every', 'cron']) {
    const path = materials.outputs[kind]
    const text = `M3-15 ${kind} 实际触发`
    const job = await createPlan({ name: `M3-15 ${kind}`, kind, path, text })
    assert.ok(job)
    await wait(() => call('GET', `/cron/jobs/${job.id}/runs?limit=200`), (rows) => rows.items.some((row) => row.task_run_id && row.trigger === 'scheduled'))
    const rows = await call('GET', `/cron/jobs/${job.id}/runs?limit=200`)
    const occurrence = rows.items.find((row) => row.task_run_id && row.trigger === 'scheduled')
    const task = await finish(occurrence.task_run_id)
    await disable(job)
    assert.equal((await file(path)).text.trim(), text)
    assert.equal(query('SELECT count(*) AS n FROM tool_calls WHERE task_run_id=? AND tool_name=? AND status=?', [task.id, 'write_file', 'completed'])[0].n, 1)
    const settled = await call('GET', `/cron/jobs/${job.id}/runs?limit=200`)
    assert.equal(settled.items.filter((row) => row.task_run_id).length, 1)
    timed.push({ job, occurrence, task, rows: settled.items })
  }
  mark(1, { timed })
  assert.ok(timed.find((entry) => entry.job.schedule.kind === 'every').rows.some((row) => row.status === 'skipped'))
  const missed = await call('POST', '/cron/jobs', { change_id: randomUUID(), workspace_id: 'office', agent_id: 'xiaowen', name: 'M3-15 暂停错过', schedule: { kind: 'at', at_ms: Date.now() + 3000, tz: 'Asia/Shanghai' }, target: { instruction: '仅回复收到，不调用工具。', execution_mode: 'new_conversation', conversation_id: null }, pre_authorized: [] })
  const supervisor = Number(execFileSync('pgrep', ['-P', String(child.pid), '-f', 'uv run python -m agentcrew_server'], { encoding: 'utf8' }).trim())
  pausedPid = Number(execFileSync('pgrep', ['-P', String(supervisor)], { encoding: 'utf8' }).trim())
  assert.ok(Number.isSafeInteger(pausedPid) && pausedPid > 0)
  process.kill(pausedPid, 'SIGSTOP')
  await new Promise((done) => setTimeout(done, 7000))
  process.kill(pausedPid, 'SIGCONT'); pausedPid = null
  await page.getByRole('button', { name: '工作台', exact: true }).click()
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  const missedRows = await wait(() => call('GET', `/cron/jobs/${missed.id}/runs?limit=200`), (rows) => rows.items.length > 0)
  assert.equal(missedRows.items.length, 1)
  assert.equal(missedRows.items[0].status, 'missed')
  assert.equal(missedRows.items[0].task_run_id, null)
  mark(2, { missed: missedRows.items, skipped: timed.find((entry) => entry.job.schedule.kind === 'every').rows.filter((row) => row.status === 'skipped') })

  const existing = await call('POST', '/cron/jobs', { change_id: randomUUID(), workspace_id: 'office', agent_id: 'xiaowen', name: 'M3-15 既有会话', schedule: { kind: 'every', every_ms: 86400000, tz: 'Asia/Shanghai' }, target: { instruction: '仅回复当前会话已收到计划指令，不调用工具。', execution_mode: 'existing', conversation_id: conversationId }, pre_authorized: [] })
  const existingOccurrence = await call('POST', `/cron/jobs/${existing.id}/run-now`, { client_request_id: randomUUID(), expected_revision: existing.revision })
  const existingTask = await finish(existingOccurrence.task_run_id)
  assert.equal(existingTask.conversation_id, conversationId)
  await disable(existing)
  mark(3, { existingTask, newConversationTask: timed[0].task })

  await automation()
  const deniedPath = `${preparedFiles}/m3-materials/outputs/denied.md`
  const denied = await createPlan({ name: 'M3-15 预授权拒绝', path: deniedPath, authorized: false, everySeconds: 86400, text: '应被权限检查阻止' })
  const deniedOccurrence = await call('POST', `/cron/jobs/${denied.id}/run-now`, { client_request_id: randomUUID(), expected_revision: denied.revision })
  const deniedTask = await wait(() => call('GET', `/task-runs/${deniedOccurrence.task_run_id}`), (task) => ['failed', 'completed'].includes(task.status))
  await disable(denied)
  assert.equal(deniedTask.status, 'failed')
  const deniedEvents = await call('GET', `/task-runs/${deniedTask.id}/events?limit=200`)
  assert.ok(deniedEvents.items.some((event) => event.type === 'tool.failed' && JSON.stringify(event.payload).includes('PRE_AUTH_EXCEEDED')))
  await assert.rejects(readFile(deniedPath), { code: 'ENOENT' })
  mark(4, { allowedTasks: timed.map((entry) => entry.task.id), deniedTask, deniedEvents })

  const skillName = `M3-15 授权范围核查 ${randomUUID().slice(0, 8)}`
  const review = await call('POST', `/task-runs/${deniedTask.id}/review`, { client_request_id: randomUUID(), instruction: `请根据这项真实 PRE_AUTH_EXCEEDED 失败核查无人值守授权与写入范围的可复用机制。真人需要归档这项机制；证据支持时请提供名为“${skillName}”的新技能建议，existing_skill_id 为 null，说明执行前应核对的条件与边界。所有结论必须引用真实事件，不能绕过授权或补写未执行结果。` })
  const reviewJob = await wait(() => call('GET', `/reviews/jobs/${review.id}`), (job) => ['completed', 'skipped', 'failed', 'cancelled'].includes(job.status), 360000)
  assert.equal(reviewJob.status, 'completed', JSON.stringify(redact(reviewJob)))
  const analysis = await call('GET', `/task-runs/${deniedTask.id}/audit-report`)
  assert.equal(analysis.report.job_id, reviewJob.id)
  const reference = analysis.report.report.root_cause_event
  assert.ok(reference, '真实报告需要可定位的原始事件引用')
  const referenced = await call('GET', `/task-runs/${deniedTask.id}/events/${reference.seq}`)
  assert.equal(referenced.global_seq, reference.global_seq)
  await page.getByRole('button', { name: '运行中心', exact: true }).click()
  await page.getByLabel('任务标识', { exact: true }).fill(deniedTask.id)
  await page.getByRole('button', { name: '打开任务记录', exact: true }).click()
  await page.getByRole('button', { name: '定位原始事件', exact: true }).click()
  await page.locator(`[data-event-seq="${reference.seq}"]`).waitFor()
  await record('run-center')
  const replayCounts = () => query('SELECT (SELECT count(*) FROM tool_calls WHERE task_run_id=?) AS tools,(SELECT count(*) FROM llm_calls l JOIN steps s ON s.id=l.step_id WHERE s.task_run_id=?) AS models', [deniedTask.id, deniedTask.id])[0]
  const beforeReplay = replayCounts()
  await page.getByRole('button', { name: '从头回放', exact: true }).click()
  await page.getByRole('button', { name: '单步前进', exact: true }).click()
  await page.getByRole('button', { name: '播放回放', exact: true }).click()
  await page.getByRole('button', { name: '暂停回放', exact: true }).click()
  const savedPosition = await page.getByTestId('replay-position').innerText()
  await page.reload()
  await page.getByTestId('replay-position').waitFor()
  assert.equal(await page.getByTestId('replay-position').innerText(), savedPosition)
  assert.deepEqual(replayCounts(), beforeReplay)
  mark(5, { reviewJob, report: analysis.report, reference, beforeReplay, savedPosition })

  assert.ok(analysis.report.report.skill_proposal, '模型未提出技能建议，该验收项不能判定通过')
  if (await page.getByRole('button', { name: '读取当前事件范围', exact: true }).isVisible()) await page.getByRole('button', { name: '读取当前事件范围', exact: true }).click()
  await page.getByRole('button', { name: '显示已读取末尾', exact: true }).click()
  while (await page.getByRole('button', { name: '加载后续事件', exact: true }).isEnabled()) {
    await page.getByRole('button', { name: '加载后续事件', exact: true }).click()
    await page.getByRole('button', { name: '显示已读取末尾', exact: true }).click()
  }
  await page.getByRole('button', { name: '确认固化为技能', exact: true }).click()
  await page.getByRole('dialog', { name: '确认发布实际技能建议', exact: true }).getByRole('button', { name: '确认固化', exact: true }).click()
  const promotionRow = await wait(() => query('SELECT j.id FROM memory_jobs j JOIN skill_promotions p ON p.job_id=j.id WHERE p.report_id=? ORDER BY j.created_at DESC', [analysis.report.id]), (rows) => rows.length > 0)
  const promotion = await wait(() => call('GET', `/reviews/jobs/${promotionRow[0].id}`), (job) => ['completed', 'failed', 'cancelled', 'interrupted'].includes(job.status), 360000)
  assert.equal(promotion.status, 'completed', JSON.stringify(redact(promotion)))
  assert.ok(promotion.skill_id && promotion.version_id)
  assert.equal(query('SELECT count(*) AS n FROM grants WHERE resource_type=? AND resource_id=? AND grantee_id=? AND revoked_at IS NULL', ['skill', promotion.skill_id, 'xiaowen'])[0].n, 0)
  const promotedSkill = await call('GET', `/skills/${promotion.skill_id}`)
  const promotedVersion = await call('GET', `/skills/${promotion.skill_id}/versions/${promotion.version_id}`)
  const ledger = query('SELECT p.report_id,p.job_id,p.version_id,v.ledger_id,l.source,l.basis,l.audit_seq,l.global_seq FROM skill_promotions p JOIN skill_versions v ON v.id=p.version_id JOIN memory_ledger l ON l.id=v.ledger_id WHERE p.job_id=?', [promotion.id])
  assert.equal(ledger.length, 1)
  assert.equal(ledger[0].report_id, analysis.report.id)
  assert.equal(JSON.parse(ledger[0].source).job_id, promotion.id)
  assert.equal(JSON.parse(ledger[0].basis).report_id, analysis.report.id)
  const skillFile = await file(`${directory}/data/skills/${promotedSkill.id}/SKILL.md`)
  assert.equal(skillFile.text, promotedVersion.content)
  assert.equal(skillFile.sha256, promotedVersion.sha256)
  await openSettings(page, '数字员工')
  await page.getByLabel('档案工作区').selectOption('office')
  await page.getByLabel('档案员工').selectOption('xiaowen')
  await page.getByRole('tab', { name: '技能与版本', exact: true }).click()
  await page.locator('.governance-table tbody tr').filter({ hasText: promotedSkill.name }).getByRole('button', { name: '查看版本', exact: true }).click()
  assert.ok((await page.getByRole('dialog').innerText()).includes(promotedVersion.version_no.toString()))
  await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click()
  await page.getByRole('tab', { name: '员工配置', exact: true }).click()
  const employee = await call('GET', '/agents/xiaowen')
  await page.locator('.governance-table tbody tr').filter({ hasText: '小文' }).getByRole('button', { name: '编辑员工', exact: true }).click()
  await page.getByLabel('员工技能引用').selectOption([...new Set([...employee.spec.skill_ids, promotedSkill.id])])
  await page.getByRole('button', { name: '保存员工', exact: true }).click()
  await record('agent-studio')
  await openSettings(page, '组织管理')
  await page.getByLabel('治理工作区').selectOption('office')
  await page.getByLabel('治理员工').selectOption('xiaowen')
  await page.getByRole('tab', { name: 'Grant授权', exact: true }).click()
  await page.getByLabel('授权资源', { exact: true }).selectOption(promotedSkill.id)
  await page.getByLabel('授权接收者').selectOption('xiaowen')
  await page.getByRole('button', { name: '授予能力', exact: true }).click()
  const grant = await wait(() => call('GET', '/grants?workspace_id=office&limit=200'), (rows) => rows.items.some((row) => row.resource_id === promotedSkill.id && row.grantee_id === 'xiaowen' && !row.revoked_at))
  await record('admin-center')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByLabel('工作区', { exact: true }).selectOption('office')
  await page.getByLabel('数字员工', { exact: true }).selectOption('xiaowen')
  await page.getByRole('textbox', { name: '任务指令', exact: true }).fill(`必须实际调用 skill_view 读取技能“${promotedSkill.name}”正文，随后依据正文说明使用条件与权限边界，禁止其他工具。`)
  await page.getByRole('button', { name: '发送任务', exact: true }).click()
  const reuseConversation = await wait(() => page.evaluate(() => sessionStorage.getItem('conversation')), (id) => id && id !== conversationId)
  const reuseRuns = await call('GET', `/conversations/${reuseConversation}/task-runs`)
  const reuseTask = await finish(reuseRuns[0].id)
  const binding = query('SELECT skill_versions FROM task_governance WHERE task_run_id=?', [reuseTask.id])[0]
  assert.equal(JSON.parse(binding.skill_versions)[promotedSkill.id], promotedVersion.id)
  assert.ok(query('SELECT call_id,status FROM tool_calls WHERE task_run_id=? AND tool_name=? AND status=? AND json_extract(input,\'$.name\')=?', [reuseTask.id, 'skill_view', 'completed', promotedSkill.name]).length > 0)
  const reads = query('SELECT * FROM skill_reads WHERE execution_id=? AND name=?', [reuseTask.id, promotedSkill.name])
  assert.ok(reads.length > 0)
  assert.ok(reads.every((entry) => entry.revision === promotedVersion.version_no))
  const snapshots = query('SELECT snapshot_id,body,sha256 FROM memory_snapshots WHERE conversation_id=? AND status=?', [reuseConversation, 'committed'])
  assert.ok(snapshots.some((entry) => JSON.parse(entry.body).skill_index.some((skill) => skill.id === promotedSkill.id && skill.version_id === promotedVersion.id)))
  mark(6, { promotion, promotedSkill, promotedVersion, ledger, grant, reuseTask, binding, reads, snapshots })

  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  const proposalName = `M3-15 员工计划 ${randomUUID().slice(0, 8)}`
  const proposalParameters = { name: proposalName, schedule: { kind: 'every', every_ms: 86400000, tz: 'Asia/Shanghai' }, instruction: '只回复真实计划已经触发，不调用工具。', execution_mode: 'new_conversation', pre_authorized: [] }
  await page.getByRole('textbox', { name: '任务指令', exact: true }).fill(`只调用一次 schedule_task，参数为 ${JSON.stringify(proposalParameters)}。只提交定时计划提案，等待真人决定，不执行其他操作。`)
  await page.getByRole('button', { name: '发送任务', exact: true }).click()
  const proposalConversation = await wait(() => page.evaluate(() => sessionStorage.getItem('conversation')), (id) => id && id !== reuseConversation)
  const sourceRuns = await call('GET', `/conversations/${proposalConversation}/task-runs`)
  const proposals = await wait(() => query('SELECT id FROM cron_proposals WHERE task_run_id=?', [sourceRuns[0].id]), (rows) => rows.length > 0)
  const pending = await call('GET', `/cron/proposals/${proposals[0].id}`)
  assert.equal(pending.status, 'pending')
  assert.equal(query('SELECT count(*) AS n FROM cron_jobs WHERE json_extract(metadata,\'$.proposal_id\')=?', [pending.id])[0].n, 0)
  await automation()
  const card = page.locator('.automation-proposal').filter({ hasText: proposalName })
  await card.getByRole('button', { name: '批准并创建唯一计划', exact: true }).click()
  const approved = await wait(() => call('GET', `/cron/proposals/${pending.id}`), (proposal) => proposal.status === 'approved')
  assert.equal(query('SELECT count(*) AS n FROM cron_jobs WHERE json_extract(metadata,\'$.proposal_id\')=?', [pending.id])[0].n, 1)
  const repeated = await call('POST', `/cron/proposals/${pending.id}`, { decision: 'allow_once', input_hash: pending.input_hash, expected_revision: pending.revision, selected: approved.selected })
  assert.equal(repeated.job_id, approved.job_id)
  const approvedJob = await call('GET', `/cron/jobs/${approved.job_id}`)
  const approvedOccurrence = await call('POST', `/cron/jobs/${approvedJob.id}/run-now`, { client_request_id: randomUUID(), expected_revision: approvedJob.revision })
  const approvedTask = await finish(approvedOccurrence.task_run_id)
  await disable(approvedJob)
  mark(7, { pending, approved, repeated, approvedTask })
  await changeIdentity(page, 'lilei')
  const rejectedRead = await request('GET', `/task-runs/${deniedTask.id}`)
  assert.equal(rejectedRead.status, 403)
  const visible = await call('GET', '/cron/jobs?workspace_id=office&limit=200')
  assert.equal(visible.items.some((job) => job.id === approvedJob.id), false)
  await changeIdentity(page, 'owner')
  await openSettings(page, '组织管理')
  await page.getByRole('tab', { name: '审计与诊断', exact: true }).click()
  await page.getByRole('button', { name: '验证审计链', exact: true }).click()
  const audit = await call('POST', '/audit/verify', {})
  assert.equal(audit.ok, true)
  result.audit = audit
  result.watermarks = query('SELECT (SELECT max(global_seq) FROM run_events) AS events,(SELECT max(seq) FROM audit_log) AS audit')[0]
  result.occurrences = query('SELECT id,job_id,task_run_id,status,retry_count,scheduled_at FROM cron_job_runs ORDER BY created_at')
  for (const input of materials.files) assert.equal((await file(input.path)).sha256, input.sha256)
  assert.deepEqual(result.errors, [])
  completed = true
} catch (error) {
  result.errors.push(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
} finally {
  try {
    if (pausedPid !== null) process.kill(pausedPid, 'SIGCONT')
    if (database) database.close()
    await closeDesktop(app, page)
  } catch (error) {
    result.errors.push(error instanceof Error ? error.message : String(error))
    process.exitCode = 1
  }
  result.finished_at = new Date().toISOString()
  result.business_checks_passed = completed && result.errors.length === 0
  result.status = result.business_checks_passed ? '平台证据待补齐' : '流程检查未通过'
  const serialized = JSON.stringify(redact(result), null, 2)
  assert.ok([...secrets].every((secret) => !serialized.includes(secret)), '验收报告不能包含机密值')
  await writeFile(`${directory}/result.json`, serialized)
  console.log(JSON.stringify({ directory, status: result.status, passed: result.checks.filter((item) => item.status === '通过').length }))
}
if (!completed) process.exitCode = 1
