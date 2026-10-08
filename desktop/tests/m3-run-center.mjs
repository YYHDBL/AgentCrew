import assert from 'node:assert/strict'
import { _electron as electron } from 'playwright-core'
import { cp, mkdir, readFile, stat, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'
import { DatabaseSync } from 'node:sqlite'

const directory = resolve('.artifacts', `m3-run-center-${Date.now()}`)
await mkdir(`${directory}/data`, { recursive: true })
await cp(resolve('../backend/data/config.json'), `${directory}/data/config.json`)
const config = JSON.parse(await readFile(`${directory}/data/config.json`, 'utf8'))
const secrets = Object.values(config.models).map((slot) => slot.api_key).filter(Boolean)
const db = new DatabaseSync(`${directory}/data/agentcrew.db`)
const before = () => ({ models: db.prepare('SELECT count(*) AS n FROM llm_calls').get().n,
  tools: db.prepare('SELECT count(*) AS n FROM tool_calls').get().n })
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`],
  executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'),
  recordHar: { path: `${directory}/network.har`, content: 'embed', mode: 'full', urlFilter: '**/api/**' } })
const page = await app.firstWindow()
page.setDefaultTimeout(60000)
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
const requests = []
page.on('request', (request) => { if (request.url().includes('/api/')) requests.push({ method: request.method(), path: new URL(request.url()).pathname }) })
const result = { directory, cases: [], screenshots: [], requests }
const fileState = async (path) => ({ sha256: createHash('sha256').update(await readFile(path)).digest('hex'), mtimeMs: (await stat(path)).mtimeMs })
const http = async (method, path, body) => page.evaluate(async ({ method, path, body }) => {
  const port = await window.agentcrew.getBackendPort()
  const token = await window.agentcrew.getToken()
  const response = await fetch(`http://127.0.0.1:${port}/api${path}`, { method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const value = await response.json()
  if (!response.ok) throw new Error(`${response.status}: ${JSON.stringify(value)}`)
  return value.data
}, { method, path, body })
const wait = async (read, predicate) => {
  const end = Date.now() + 180000
  while (Date.now() < end) {
    const value = await read()
    if (predicate(value)) return value
    await new Promise((done) => setTimeout(done, 100))
  }
  throw new Error('真实运行未达到验收状态')
}
try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  await page.getByRole('button', { name: '运行中心', exact: true }).click()
  await page.getByRole('heading', { name: '运行中心', exact: true }).waitFor()
  // 通过真实管理 API 导入已有辅助模型生成的岗位记忆，保留来源证明。
  const sourceDb = new DatabaseSync(resolve('../data/m3-intermediate/02-api-acceptance/identity-http0/agentcrew.db'), { readOnly: true })
  const soul = sourceDb.prepare("SELECT id,model,result,task_run_id FROM memory_soul_generations WHERE agent_id='xiaowen' AND status='completed' ORDER BY created_at LIMIT 1").get()
  sourceDb.close()
  assert.ok(soul?.result)
  result.soul_source = { id: soul.id, model: soul.model, task_run_id: soul.task_run_id, sha256: createHash('sha256').update(soul.result).digest('hex') }
  await http('POST', '/memory/stores/soul/xiaowen?workspace_id=office&agent_id=xiaowen', { change_id: randomUUID(), expected_revision: 0,
    basis: `所有者为运行中心验收导入已完成的实际岗位记忆，来源生成 ${soul.id}`, text: soul.result })
  const initialized = await http('POST', '/conversations', { workspace_id: 'office', agent_id: 'xiaowen', client_request_id: randomUUID(), instruction: '仅用一句中文确认收到真实运行中心初始化指令，禁止调用工具。' })
  const initialization = await wait(() => http('GET', `/task-runs/${initialized.task_run_id}`), (value) => ['completed', 'failed', 'cancelled'].includes(value.status))
  assert.equal(initialization.status, 'completed', JSON.stringify(initialization))
  const workspace = await http('GET', '/workspaces/office')
  const recovered = await http('POST', '/conversations', { workspace_id: 'office', agent_id: 'xiaowen', client_request_id: randomUUID(),
    instruction: '请首先实际调用ask_user，询问用户是否创建实际验收文件，等待真实回答。得到同意后，实际调用write_file，在当前工作区创建m3-11/recovered.txt，正文为M3-11 recovered actual file，然后调用read_file核对正文。只有文件实际写入且读取一致才完成任务。中断恢复后没有收到有效回答时继续询问。' })
  await wait(() => http('GET', `/conversations/${recovered.conversation.id}/questions`), (value) => value.length > 0)
  await page.getByRole('textbox', { name: '任务标识', exact: true }).fill(recovered.task_run_id)
  await page.getByRole('button', { name: '打开任务记录', exact: true }).click()
  await page.getByRole('button', { name: '从头回放', exact: true }).click()
  await page.getByTestId('replay-position').filter({ hasText: '历史位置 seq 0' }).waitFor()
  const oldPort = await page.evaluate(() => window.agentcrew.getBackendPort())
  const processLine = execFileSync('ps', ['-axo', 'pid,command'], { encoding: 'utf8' }).split('\n').find((line) => line.includes('/python3 -m agentcrew_server') && line.includes(directory))
  assert.ok(processLine)
  const killedPid = Number(processLine.trim().split(/\s+/)[0])
  process.kill(killedPid, 'SIGKILL')
  await wait(() => page.evaluate(() => window.agentcrew.getBackendPort()), (port) => port && port !== oldPort)
  assert.equal((await http('GET', `/task-runs/${recovered.task_run_id}`)).status, 'interrupted')
  await http('POST', `/task-runs/${recovered.task_run_id}/resume`, {})
  const completed = await wait(async () => {
    for (const question of await http('GET', `/conversations/${recovered.conversation.id}/questions`)) {
      await http('POST', `/questions/${question.request_id}/answer`, { answer: '同意创建当前工作区文件，正文M3-11 recovered actual file，然后实际读取核查。' })
    }
    for (const card of (await http('GET', `/task-runs/${recovered.task_run_id}/approvals`)).filter((card) => !card.stale)) {
      await http('POST', `/tool-approvals/${card.call_id}`, { decision: 'allow_once', input_hash: card.input_hash })
    }
    return http('GET', `/task-runs/${recovered.task_run_id}`)
  }, (value) => ['completed', 'failed', 'cancelled'].includes(value.status))
  assert.equal(completed.status, 'completed', JSON.stringify(completed))
  const recoveredAttempts = await http('GET', `/task-runs/${recovered.task_run_id}/attempts`)
  assert.equal(recoveredAttempts.length, 2)
  const actualScope = await http('GET', `/conversations/${completed.conversation_id}/scope`)
  const actualFile = `${actualScope.workspace_dir}/m3-11/recovered.txt`
  result.actual_file_content = await readFile(actualFile, 'utf8')
  assert.equal(result.actual_file_content.trim(), 'M3-11 recovered actual file')
  result.recovery = { task_run_id: recovered.task_run_id, killed_pid: killedPid, old_port: oldPort, new_port: await page.evaluate(() => window.agentcrew.getBackendPort()), attempts: recoveredAttempts }
  await page.getByRole('textbox', { name: '任务标识', exact: true }).fill(recovered.task_run_id)
  await page.getByRole('button', { name: '打开任务记录', exact: true }).click()
  await page.getByRole('button', { name: '读取当前事件范围', exact: true }).waitFor()
  assert.ok((await page.getByTestId('replay-position').textContent()).includes('历史位置 seq 0'))
  await page.getByRole('button', { name: '读取当前事件范围', exact: true }).click()
  await page.getByRole('combobox', { name: '运行尝试', exact: true }).selectOption('2')
  await page.getByRole('button', { name: '从头回放', exact: true }).waitFor()
  await page.getByRole('button', { name: '单步前进', exact: true }).click()
  await page.locator('[data-event-seq]').first().waitFor()
  assert.ok((await page.locator('[data-event-seq]').allTextContents()).every((text) => text.includes('尝试 2')))
  result.cases.push('真实 SIGKILL 恢复、文件完成和第二次尝试时间线')
  await page.getByRole('button', { name: '工作台', exact: true }).click()
  const conversation = (await http('GET', '/conversations')).find((item) => item.id === completed.conversation_id)
  assert.ok(conversation)
  const label = conversation.title || await page.evaluate((date) => `任务 ${new Date(date).toLocaleString('zh-CN')}`, conversation.last_activity_at)
  await page.locator('.recent-tasks button').filter({ hasText: label }).click()
  await page.getByRole('button', { name: '进入此任务运行记录', exact: true }).click()
  await page.getByRole('textbox', { name: '任务标识', exact: true }).waitFor()
  assert.equal(await page.getByRole('textbox', { name: '任务标识', exact: true }).inputValue(), recovered.task_run_id)
  result.cases.push('工作台完成任务进入同一 TaskRun 记录')
  const plan = await http('POST', '/cron/jobs', { change_id: randomUUID(), workspace_id: 'office', agent_id: 'xiaowen', name: '真实运行中心失败验收',
    schedule: { kind: 'every', every_ms: 86400000, tz: 'UTC' }, enabled: true, pre_authorized: [],
    target: { execution_mode: 'new_conversation', conversation_id: null, instruction: `请实际调用read_file读取资料文件 ${workspace.data_dir}/m3-run-center-input-${randomUUID()}.txt，提取资料第一行，禁止其他工具。` } })
  const occurrence = await http('POST', `/cron/jobs/${plan.id}/run-now`, { client_request_id: randomUUID(), expected_revision: 1 })
  result.cron_job_id = plan.id
  result.occurrence_id = occurrence.id
  await wait(() => http('GET', `/task-runs/${occurrence.task_run_id}`), (value) => value.status === 'failed')
  await http('PATCH', `/cron/jobs/${plan.id}`, { change_id: randomUUID(), expected_revision: 1, enabled: false })
  const report = await wait(() => http('GET', `/task-runs/${occurrence.task_run_id}/audit-report`), (value) => value.report !== null)
  const target = { task_run_id: occurrence.task_run_id, report: JSON.stringify(report.report.report) }
  result.task_run_id = target.task_run_id
  result.report_id = report.report.id
  result.review_job_id = report.job?.id
  await page.getByRole('textbox', { name: '任务标识', exact: true }).fill(target.task_run_id)
  await page.getByRole('button', { name: '打开任务记录', exact: true }).click()
  await page.getByText(target.task_run_id, { exact: true }).first().waitFor()
  await page.getByRole('heading', { name: '模型分析报告', exact: true }).waitFor()
  await page.getByRole('button', { name: '定位原始事件', exact: true }).click()
  const reference = JSON.parse(target.report).root_cause_event
  await page.locator(`[data-event-seq="${reference.seq}"]`).waitFor()
  result.cases.push('真实失败报告与原事件定位')
  await page.locator(`[data-event-seq="${reference.seq}"]`).getByRole('button', { name: '查看调用明细', exact: true }).click()
  await page.getByRole('dialog', { name: '真实模型与工具调用', exact: true }).waitFor()
  assert.ok((await page.getByRole('dialog', { name: '真实模型与工具调用', exact: true }).textContent()).includes('NOT_FOUND'))
  await page.getByRole('dialog', { name: '真实模型与工具调用', exact: true }).getByRole('button', { name: 'Close', exact: true }).click()
  result.cases.push('实际工具参数、结果与调用标识明细')
  const start = before()
  const startFile = await fileState(actualFile)
  const requestIndex = requests.length
  await page.getByRole('button', { name: '从头回放', exact: true }).click()
  await page.getByRole('button', { name: '单步前进', exact: true }).click()
  await page.getByRole('button', { name: '播放回放', exact: true }).click()
  await page.getByRole('button', { name: '暂停回放', exact: true }).click()
  const cursor = await page.getByTestId('replay-position').textContent()
  const frozen = await page.getByRole('region', { name: '实时事件尾部', exact: true }).textContent()
  await page.reload()
  await page.getByTestId('replay-position').waitFor()
  assert.equal(await page.getByTestId('replay-position').textContent(), cursor)
  assert.equal((await page.getByRole('region', { name: '实时事件尾部', exact: true }).textContent()).split(' · ')[0], frozen.split(' · ')[0])
  assert.deepEqual(before(), start)
  assert.deepEqual(await fileState(actualFile), startFile)
  result.file_replay = { path: actualFile, before: startFile, after: await fileState(actualFile) }
  assert.equal(requests.slice(requestIndex).filter((request) => request.method !== 'GET').length, 0)
  result.cases.push('回放播放暂停单步与刷新，模型工具和写API没有增加')
  await page.getByRole('button', { name: '加载后续事件', exact: true }).click()
  result.cases.push('真实事件分页')
  for (const [width, height, name] of [[1440, 900, 'wide'], [960, 900, 'narrow']]) {
    await page.setViewportSize({ width, height })
    await page.getByRole('button', { name: '从头回放', exact: true }).focus()
    assert.equal(await page.getByRole('button', { name: '从头回放', exact: true }).evaluate((node) => node === document.activeElement), true)
    const screenshot = `${directory}/run-center-${name}.png`
    await page.screenshot({ path: screenshot })
    result.screenshots.push(screenshot)
  }
  result.cases.push('宽窄窗口与键盘焦点')
  await page.getByLabel('演示身份').selectOption('lilei')
  await wait(() => page.getByLabel('演示身份').inputValue(), (value) => value === 'lilei')
  await page.getByRole('textbox', { name: '任务标识', exact: true }).fill(target.task_run_id)
  await page.getByRole('button', { name: '打开任务记录', exact: true }).click()
  await page.getByRole('alert').first().waitFor()
  result.permission_error = await page.getByRole('alert').first().textContent()
  assert.equal(await page.locator('[data-event-seq]').count(), 0)
  assert.equal(await page.getByRole('heading', { name: '模型分析报告', exact: true }).count(), 0)
  assert.equal(await page.evaluate(() => sessionStorage.getItem('run-center-task')), null)
  result.cases.push('真实 member 身份被拒绝，事件、报告与保存任务标识清理')
  result.event_watermark = db.prepare('SELECT max(global_seq) AS n FROM run_events').get().n
  result.audit_sequence = db.prepare('SELECT max(seq) AS n FROM audit_log').get().n
  result.replay_counts = { before: start, after: before() }
  const raw = JSON.stringify(result, null, 2)
  assert.deepEqual(errors, [])
  assert.ok(secrets.every((secret) => !raw.includes(secret)))
  await writeFile(`${directory}/result.json`, raw)
  console.log(JSON.stringify({ directory, cases: result.cases.length, task_run_id: target.task_run_id }))
} finally {
  await app.close()
  db.close()
}
