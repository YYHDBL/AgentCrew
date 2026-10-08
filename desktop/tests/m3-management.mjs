import assert from 'node:assert/strict'
import { _electron as electron } from 'playwright-core'
import { cp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { randomUUID } from 'node:crypto'
import { DatabaseSync } from 'node:sqlite'
import { createServer } from 'node:http'

const directory = resolve('.artifacts', `m3-management-${Date.now()}`)
await mkdir(`${directory}/data`, { recursive: true })
await cp('../backend/data/config.json', `${directory}/data/config.json`)
const db = new DatabaseSync(`${directory}/data/agentcrew.db`)
const upstream = new DatabaseSync(`${directory}/connector.sqlite`)
upstream.exec('CREATE TABLE requests(id INTEGER PRIMARY KEY,method TEXT,path TEXT,received_at TEXT)')
const service = createServer((request, response) => {
  const receipt = upstream.prepare('INSERT INTO requests(method,path,received_at) VALUES(?,?,?)').run(request.method, request.url, new Date().toISOString())
  response.setHeader('Content-Type', 'application/json')
  response.end(JSON.stringify(upstream.prepare('SELECT * FROM requests WHERE id=?').get(receipt.lastInsertRowid)))
})
await new Promise((done) => service.listen(0, '127.0.0.1', done))
const endpoint = `http://127.0.0.1:${service.address().port}`
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`],
  executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'),
  recordHar: { path: `${directory}/network.har`, content: 'embed', mode: 'full', urlFilter: '**/api/**' } })
const page = await app.firstWindow()
page.setDefaultTimeout(60000)
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
const result = { directory, cases: [], screenshots: [], requests: [] }
const request = async (method, path, body, owner = false) => {
  const actual = await page.evaluate(async ({ method, path, body, owner }) => {
    const port = await window.agentcrew.getBackendPort()
    const token = await window.agentcrew.getToken()
    const identity = owner ? null : sessionStorage.getItem('agentcrew-identity')
    const response = await fetch(`http://127.0.0.1:${port}/api${path}`, { method,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...(identity ? { 'X-AgentCrew-Identity': identity } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body) })
    return { status: response.status, response: await response.json() }
  }, { method, path, body, owner })
  result.requests.push({ method, path, request: body, ...actual })
  return { status: actual.status, value: actual.response.data }
}
const wait = async (read, accepts) => {
  const deadline = Date.now() + 240000
  while (Date.now() < deadline) {
    const value = await read()
    if (accepts(value)) return value
    await new Promise((done) => setTimeout(done, 100))
  }
  throw new Error('真实员工管理状态未在期限内满足验收条件')
}
const screenshot = async (name) => {
  const path = `${directory}/${name}.png`
  await page.screenshot({ path })
  result.screenshots.push(path)
}
const run = async (agent, instruction) => {
  const created = await request('POST', '/conversations', { workspace_id: 'office', agent_id: agent, instruction, client_request_id: randomUUID() })
  assert.equal(created.status, 201)
  const task = await wait(async () => (await request('GET', `/task-runs/${created.value.task_run_id}`)).value,
    (value) => ['completed', 'failed', 'cancelled'].includes(value.status))
  assert.equal(task.status, 'completed', JSON.stringify(task))
  return task
}
try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  await page.getByRole('button', { name: '数字员工管理', exact: true }).click()
  await page.getByRole('heading', { name: '数字员工管理', exact: true }).waitFor()
  await page.getByLabel('档案工作区').selectOption('office')
  await page.getByRole('tab', { name: '员工配置', exact: true }).click()
  await page.getByRole('button', { name: '创建员工', exact: true }).click()
  const employeeName = `真实档案员工-${randomUUID().slice(0, 8)}`
  await page.getByLabel('员工名称').fill(employeeName)
  await page.getByLabel('员工岗位').fill('核查当前材料与实际工具结果，严格遵守权限边界。')
  await page.getByRole('button', { name: '保存员工', exact: true }).click()
  const employee = await wait(async () => (await request('GET', '/agents?workspace_id=office')).value.items.find((row) => row.name === employeeName), Boolean)
  await page.getByLabel('档案员工').selectOption(employee.id)
  result.employee_id = employee.id
  result.cases.push('通过员工配置创建真实员工与模型槽')

  const sourceDb = new DatabaseSync(resolve('../data/m3-intermediate/02-api-acceptance/identity-http0/agentcrew.db'), { readOnly: true })
  const soul = sourceDb.prepare("SELECT id,model,result FROM memory_soul_generations WHERE status='completed' ORDER BY created_at LIMIT 1").get()
  sourceDb.close()
  assert.ok(soul?.result)
  assert.equal((await request('POST', `/memory/stores/soul/${employee.id}?workspace_id=office&agent_id=${employee.id}`, {
    change_id: randomUUID(), expected_revision: 0, basis: `所有者导入实际岗位记忆，来源辅助生成${soul.id}`, text: soul.result })).status, 200)
  const oldTask = await run(employee.id, '仅用一句中文确认收到当前实际岗位指令，禁止工具调用。')
  result.old_task = oldTask

  await page.getByRole('tab', { name: '员工配置', exact: true }).click()
  const employeeRow = page.locator('.governance-table tbody tr').filter({ hasText: employeeName })
  await employeeRow.getByRole('button', { name: '编辑员工', exact: true }).click()
  await page.getByLabel('员工岗位').fill('逐项核查实际资料来源，保存真实校验依据。')
  await page.getByLabel('员工模型槽').selectOption('aux')
  await page.getByRole('button', { name: '保存员工', exact: true }).click()
  const newTask = await run(employee.id, '仅用一句中文确认当前岗位要求逐项核查资料来源，禁止工具调用。')
  assert.notDeepEqual(newTask.agent_spec_snapshot, oldTask.agent_spec_snapshot)
  assert.deepEqual((await request('GET', `/task-runs/${oldTask.id}`)).value.agent_spec_snapshot, oldTask.agent_spec_snapshot)
  result.new_task = newTask
  result.cases.push('岗位及模型槽修改影响新任务，历史快照保持完整')

  for (const [label, kind, text] of [['USER 记忆', 'user', '真实员工档案核验偏好：依据原始材料说明来源。'], ['工作区记忆', 'workspace', '真实员工档案材料保存在当前工作区。'], ['员工 soul', 'soul', '当前员工核查来源时保留实际事件依据。']]) {
    await page.getByRole('tab', { name: label, exact: true }).click()
    await page.getByRole('button', { name: '新建条目', exact: true }).click()
    await page.getByLabel('记忆正文', { exact: true }).fill(text)
    await page.getByLabel('保存依据', { exact: true }).fill('所有者在真实员工档案中配置长期资料')
    await page.getByRole('button', { name: '保存记忆', exact: true }).click()
    await page.getByRole('tabpanel', { name: label }).getByText('记忆已保存', { exact: true }).waitFor()
    const store = await wait(async () => (await request('GET', `/memory/stores/${kind}/${kind === 'user' ? 'owner' : kind === 'workspace' ? 'office' : employee.id}?workspace_id=office&agent_id=${employee.id}`)).value,
      (value) => value?.entries?.some((entry) => entry.text === text))
    assert.ok(store.entries.some((entry) => entry.text === text))
  }
  result.cases.push('员工档案接入真实三库与记忆账本')

  await page.getByRole('tab', { name: '员工配置', exact: true }).click()
  await page.getByRole('tab', { name: '技能与版本', exact: true }).click()
  await page.getByRole('button', { name: '创建技能', exact: true }).click()
  const skillName = `真实档案技能-${randomUUID().slice(0, 8)}`
  await page.getByLabel('资源名称').fill(skillName)
  await page.getByLabel('技能描述').fill('核查资料来源并保留实际证据')
  await page.getByLabel('技能正文', { exact: true }).fill('# 资料来源核查\n\n读取原始资料，记录路径、SHA、真实事件及核查结果。')
  await page.getByRole('button', { name: '保存资源', exact: true }).click()
  const skill = await wait(async () => (await request('GET', '/skills?workspace_id=office')).value.items.find((row) => row.name === skillName), Boolean)
  const skillRow = page.locator('.governance-table tbody tr').filter({ hasText: skillName })
  await skillRow.getByRole('button', { name: '查看版本', exact: true }).click()
  await page.getByLabel('版本正文').fill('# 资料来源核查\n\n核对原始路径、SHA与事件水位，说明实际核查结论。')
  await page.getByLabel('版本修改依据').fill('所有者为当前员工保存明确的资料核查机制')
  await page.getByRole('button', { name: '发布新版本', exact: true }).click()
  const versions = await wait(async () => (await request('GET', `/skills/${skill.id}/versions`)).value.items, (items) => items.length === 2)
  result.skill_versions = versions
  await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click()
  result.cases.push('统一技能服务发布不可变新版本')

  await page.reload()
  await page.getByRole('heading', { name: '数字员工管理', exact: true }).waitFor()
  await wait(() => page.getByLabel('档案工作区').inputValue(), (value) => value === 'office')
  await wait(() => page.getByLabel('档案员工').inputValue(), (value) => value === employee.id)
  await page.getByRole('tab', { name: '员工配置', exact: true }).click()
  await page.locator('.governance-table tbody tr').filter({ hasText: employeeName }).first().waitFor()
  result.cases.push('刷新后档案范围、员工列表与技能版本状态一致')

  await page.getByRole('button', { name: '管理中心', exact: true }).click()
  await page.getByLabel('治理工作区').selectOption('office')
  await page.getByLabel('治理员工').selectOption(employee.id)
  await page.getByRole('tab', { name: 'Grant授权', exact: true }).click()
  await page.getByLabel('授权资源', { exact: true }).selectOption(skill.id)
  await page.getByLabel('授权接收者').selectOption(employee.id)
  await page.getByRole('button', { name: '授予能力', exact: true }).click()
  const grant = await wait(async () => (await request('GET', '/grants?workspace_id=office')).value.items.find((row) => row.resource_id === skill.id && row.grantee_id === employee.id && !row.revoked_at), Boolean)
  const grantRow = page.locator('.governance-table tbody tr').filter({ hasText: skill.id })
  await grantRow.getByRole('button', { name: '撤销授权', exact: true }).click()
  await page.getByRole('button', { name: '确认操作', exact: true }).click()
  await wait(async () => (await request('GET', '/grants?workspace_id=office&revoked=true')).value.items.find((row) => row.id === grant.id)?.revoked_at, Boolean)
  await page.getByRole('tab', { name: '员工规则', exact: true }).click()
  await page.getByLabel('规则工具').fill('read_file')
  await page.getByLabel('规则匹配范围').fill(`${directory}/materials`)
  await page.getByRole('button', { name: '保存规则', exact: true }).click()
  await page.locator('.governance-table tbody tr').filter({ hasText: `${directory}/materials` }).getByRole('button', { name: '回收规则', exact: true }).click()
  await page.getByRole('button', { name: '确认操作', exact: true }).click()
  const rules = await wait(async () => (await request('GET', `/agents/${employee.id}/permission-rules?revoked=true`)).value.items, (items) => items.some((rule) => rule.pattern === `${directory}/materials` && rule.revoked_at))
  result.rules = rules
  result.cases.push('真实Grant独立授予及撤销，权限规则回收')

  await page.getByRole('tab', { name: '连接器', exact: true }).click()
  await page.getByRole('button', { name: '创建连接器', exact: true }).click()
  await page.getByLabel('资源名称').fill('实际HTTP档案校验')
  await page.getByLabel('连接器配置JSON').fill(JSON.stringify({ url: endpoint, allowed_hosts: ['127.0.0.1'], allowed_ports: [service.address().port], allow_loopback: true }))
  await page.getByRole('button', { name: '保存资源', exact: true }).click()
  await page.locator('.governance-table tbody tr').filter({ hasText: '实际HTTP档案校验' }).getByRole('button', { name: '验证连接', exact: true }).click()
  await wait(() => upstream.prepare('SELECT count(*) AS n FROM requests').get().n, (count) => count > 0)
  result.connector_requests = upstream.prepare('SELECT * FROM requests').all()
  await screenshot('admin-center')
  await page.getByRole('button', { name: '数字员工管理', exact: true }).click()
  const futureAt = Date.now() + 2 * 60 * 60 * 1000
  const cronCreated = await request('POST', '/cron/jobs', { change_id: randomUUID(), workspace_id: 'office', agent_id: employee.id, name: '员工档案真实计划核查',
    schedule: { kind: 'at', at_ms: futureAt, tz: 'Asia/Shanghai' }, target: { instruction: '核对员工档案中的定时任务记录。', execution_mode: 'new_conversation', conversation_id: null }, pre_authorized: [] })
  assert.equal(cronCreated.status, 201)
  const cronJob = cronCreated.value
  await page.getByRole('tab', { name: '员工档案', exact: true }).click()
  await page.getByText('员工档案真实计划核查', { exact: false }).first().waitFor()
  assert.ok((await page.getByText('创建来源user', { exact: false }).count()) > 0)
  result.cron_job = { id: cronJob.id, name: cronJob.name, next_run_at: cronJob.state.next_run_at, agent: cronJob.metadata.agent_id }
  result.cases.push('员工档案定时任务来自真实服务端记录')
  await screenshot('agent-studio')
  result.cases.push('管理中心实际HTTP连接校验与员工档案')

  await page.getByLabel('演示身份').selectOption('lilei')
  await wait(() => page.getByLabel('演示身份').inputValue(), (value) => value === 'lilei')
  await page.getByRole('tab', { name: '员工配置', exact: true }).click()
  await page.getByText('当前member角色仅能查看已授权资源', { exact: false }).first().waitFor()
  assert.equal(await page.getByRole('button', { name: '创建员工', exact: true }).isEnabled(), false)
  assert.equal((await request('POST', '/agents', { change_id: randomUUID(), expected_revision: 0, workspace_id: 'default', name: '权限拒绝核查', spec: employee.spec })).status, 403)
  result.cases.push('member管理操作禁用与真实403')
  await page.getByLabel('演示身份').selectOption('owner')
  await wait(() => page.getByLabel('演示身份').inputValue(), (value) => value === 'owner')
  await page.getByRole('button', { name: '管理中心', exact: true }).click()
  await page.getByRole('tab', { name: '审计与诊断', exact: true }).click()
  await wait(() => db.prepare("SELECT count(*) AS n FROM memory_jobs WHERE status IN ('queued','running','waiting_approval','cancelling')").get().n, (value) => value === 0)
  await page.getByRole('button', { name: '创建验证备份', exact: true }).click()
  await wait(async () => (await request('GET', '/diagnostics/backups')).value, (value) => value.some((backup) => backup.verified))
  const lastAudit = db.prepare('SELECT max(seq) AS n FROM audit_log').get().n
  db.prepare('UPDATE audit_log SET detail=? WHERE seq=?').run(JSON.stringify({ independent_library_tamper: randomUUID() }), lastAudit)
  await page.getByRole('button', { name: '验证审计链', exact: true }).click()
  await page.getByRole('heading', { name: '只读诊断', exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: '数字员工管理', exact: true }).isEnabled(), false)
  await app.evaluate(({ BrowserWindow }, path) => {
    const session = BrowserWindow.getAllWindows()[0].webContents.session
    globalThis.managementDownloadResult = null
    session.once('will-download', (_event, item) => {
      item.setSavePath(path)
      item.once('done', (_doneEvent, state) => { globalThis.managementDownloadResult = { state, filename: item.getFilename() } })
    })
  }, `${directory}/audit-export.json`)
  await page.getByRole('button', { name: '导出审计', exact: true }).click()
  const download = await wait(() => app.evaluate(() => globalThis.managementDownloadResult), (value) => value?.state === 'completed')
  const exportedRows = JSON.parse(await readFile(`${directory}/audit-export.json`, 'utf8'))
  assert.ok(exportedRows.rows.length > 0 && exportedRows.rows.every((row) => Number.isSafeInteger(row.seq)))
  result.audit_export = { filename: download.filename, rows: exportedRows.rows.length }
  await screenshot('diagnostic')
  await page.getByRole('button', { name: '恢复此备份', exact: true }).first().click()
  await page.getByRole('button', { name: '确认操作', exact: true }).click()
  await page.getByRole('button', { name: '重启并完整验证', exact: true }).click()
  await wait(async () => {
    try { return (await request('GET', '/diagnostics')).value?.mode } catch { return null }
  }, (value) => value === 'normal')
  await page.getByText('内部链：通过', { exact: true }).waitFor()
  await page.getByText('锚点：通过', { exact: true }).waitFor()
  await screenshot('restored')
  result.cases.push('独立验收库篡改后的只读诊断、导出、受控恢复与完整验证')

  await page.getByRole('button', { name: '数字员工管理', exact: true }).click()
  await page.getByRole('heading', { name: '数字员工管理', exact: true }).waitFor()
  await page.setViewportSize({ width: 960, height: 900 })
  await page.getByRole('tab', { name: '员工档案', exact: true }).click()
  await page.getByRole('tab', { name: '员工配置', exact: true }).click()
  await page.setViewportSize({ width: 760, height: 600 })
  const createButton = page.getByRole('button', { name: '创建员工', exact: true })
  await createButton.focus()
  await page.keyboard.press('Enter')
  await page.getByRole('dialog').waitFor()
  await page.getByLabel('员工名称').fill('键盘取消核查')
  await page.keyboard.press('Escape')
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  assert.equal(await createButton.evaluate((element) => document.activeElement === element), true)
  await page.setViewportSize({ width: 1440, height: 900 })
  result.cases.push('宽窄窗口与键盘打开、取消及焦点返回有效')
  assert.deepEqual(errors, [])
  db.close()
  const restoredDb = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
  result.event_watermark = restoredDb.prepare('SELECT max(global_seq) AS n FROM run_events').get().n
  result.audit_sequence = restoredDb.prepare('SELECT max(seq) AS n FROM audit_log').get().n
  result.restored_audit_rows = restoredDb.prepare('SELECT count(*) AS n FROM audit_log').get().n
  restoredDb.close()
  await writeFile(`${directory}/result.json`, JSON.stringify(result, null, 2))
  console.log(JSON.stringify({ directory, cases: result.cases.length }))
} finally {
  await app.close()
  await new Promise((done) => service.close(done))
  upstream.close()
  try { db.close() } catch { /* 恢复验证后已经关闭 */ }
}
