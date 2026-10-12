import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { cp, mkdir, readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { _electron as electron } from 'playwright-core'
import { evidence } from './helpers/evidence.mjs'
import { recordDesktopEvidence } from './helpers/desktop.mjs'

const directory = resolve('.artifacts', `workbench-${Date.now()}`)
await mkdir(`${directory}/data`, { recursive: true })
await cp('../backend/data/config.json', `${directory}/data/config.json`)
const proof = await evidence(directory)
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`], executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const child = app.process()
const page = await app.firstWindow()
proof.attach(page)
page.setDefaultTimeout(120000)
const errors = []
const queries = ['SELECT id,conversation_id,status,current_attempt_no FROM task_runs',
  'SELECT call_id,task_run_id,tool_name,input,status FROM tool_calls',
  'SELECT l.id,s.task_run_id,l.model,l.error FROM llm_calls l JOIN steps s ON s.id=l.step_id',
  'SELECT global_seq,task_run_id,seq,type,payload FROM run_events ORDER BY global_seq',
  'SELECT seq,action,resource_type,resource_id,hash FROM audit_log ORDER BY seq']
page.on('pageerror', (error) => errors.push(error.message))

const call = (method, path, body) => page.evaluate(async ({ method, path, body }) => {
  const port = await window.agentcrew.getBackendPort()
  const token = await window.agentcrew.getToken()
  if (!port || !token) throw new Error('真实任务服务尚未就绪')
  const response = await fetch(`http://127.0.0.1:${port}/api${path}`, {
    method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  const envelope = response.status === 204 ? {} : await response.json()
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${envelope.error?.message}`)
  return envelope.data
}, { method, path, body })

const wait = async (read, accept, timeout = 120000) => {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const value = await read()
    if (accept(value)) return value
    await new Promise((done) => setTimeout(done, 200))
  }
  throw new Error('真实工作台状态未在期限内达到检查条件')
}

try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  await page.getByRole('heading', { name: '今天，需要处理什么？', exact: true }).waitFor()
  assert.equal(await page.locator('.run-details').count(), 0)
  const sidebar = await page.locator('.navigation').boundingBox()
  const history = await page.locator('.task-list').boundingBox()
  assert.ok(history.x >= sidebar.x && history.x + history.width <= sidebar.x + sidebar.width + 1)
  await page.getByLabel('工作区', { exact: true }).selectOption('office')
  await page.getByLabel('数字员工', { exact: true }).selectOption('xiaowen')

  const source = new DatabaseSync(resolve('../data/m3-intermediate/02-api-acceptance/identity-http0/agentcrew.db'), { readOnly: true })
  const soul = source.prepare("SELECT id,model,task_run_id,result FROM memory_soul_generations WHERE status='completed' ORDER BY created_at LIMIT 1").get()
  source.close()
  assert.ok(soul?.result)
  await call('POST', '/memory/stores/soul/xiaowen?workspace_id=office&agent_id=xiaowen', {
    change_id: randomUUID(), expected_revision: 0, text: soul.result,
    basis: `真实界面检查使用生成记录 ${soul.id}（${soul.model}，TaskRun ${soul.task_run_id}）。`
  })

  const composer = page.getByRole('textbox', { name: '任务指令', exact: true })
  await composer.fill('键盘检查')
  await composer.press('Shift+Enter')
  assert.equal(await composer.inputValue(), '键盘检查\n')
  const instruction = '请直接生成一份 Markdown 项目材料整理说明。必须包含标题“材料整理说明”、加粗重点、三个列表项、五行三列的文件检查表格，以及一个包含 print("完成") 的 python 代码块。随后用八个完整段落解释整理方法，每段不少于五十个汉字。只返回说明正文，不使用任何工具。'
  await composer.fill(instruction)
  await page.getByRole('button', { name: '发送任务', exact: true }).click()
  const conversationId = await wait(() => page.evaluate(() => sessionStorage.getItem('conversation')), Boolean)
  await wait(() => call('GET', `/conversations/${conversationId}/task-runs`), (runs) => runs.at(-1)?.status === 'completed')
  await page.locator('.message.assistant .markdown-body table').waitFor()
  await page.locator('.message.assistant .code-block').waitFor()
  assert.ok(await page.locator('.message.assistant .markdown-body strong').count() > 0)
  assert.ok(await page.locator('.message.assistant .markdown-body li').count() > 0)
  assert.equal(await page.locator('.message.assistant').evaluate((element) => getComputedStyle(element).backgroundColor), 'rgba(0, 0, 0, 0)')
  await page.locator('.task-row-title').filter({ hasText: 'Markdown 项目材料整理说明' }).waitFor()
  assert.ok((await page.locator('.task-heading').innerText()).includes('已完成'))
  assert.equal(await page.locator('.run-details').count(), 0)
  await page.getByRole('button', { name: '复制代码', exact: true }).click()
  const copiedCode = await page.evaluate(() => navigator.clipboard.readText())
  assert.ok(copiedCode.includes('print('))
  await page.locator('.message.assistant').getByRole('button', { name: '复制回复', exact: true }).click()
  assert.ok((await page.evaluate(() => navigator.clipboard.readText())).includes('材料整理说明'))
  await page.locator('.content-scroll').evaluate((element) => element.scrollTo({ top: 0 }))
  await page.getByRole('button', { name: '返回最新内容', exact: true }).waitFor()
  await page.getByRole('button', { name: '返回最新内容', exact: true }).click()
  await page.waitForFunction(() => {
    const content = document.querySelector('.content-scroll')
    return content.scrollHeight - content.scrollTop - content.clientHeight < 80
  })

  await page.getByRole('button', { name: '展开运行详情', exact: true }).click()
  await page.locator('.run-details').waitFor()
  assert.ok((await page.locator('.run-details').innerText()).includes('理解任务与组织回复'))
  await page.getByRole('button', { name: '收起运行详情', exact: true }).last().click()
  await page.getByLabel('搜索任务', { exact: true }).fill('不存在的查询内容')
  await page.getByText('没有符合条件的任务。', { exact: true }).waitFor()
  await page.getByLabel('搜索任务', { exact: true }).fill('')
  await page.reload()
  await page.locator('.message.assistant .markdown-body table').waitFor()
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByRole('heading', { name: '今天，需要处理什么？', exact: true }).waitFor()

  const rules = await call('GET', '/agents/xiaowen/permission-rules')
  const rule = rules.items.find((item) => item.id === 'seed-office-write')
  assert.ok(rule)
  await call('DELETE', `/agents/xiaowen/permission-rules/${rule.id}`, { change_id: randomUUID(), expected_revision: rule.revision })
  const workspace = await call('GET', '/workspaces/office')
  const path = `${workspace.data_dir}/files/界面检查材料.md`
  await composer.fill(`只使用 write_file 在 ${path} 写入“界面真实产物检查”，随后使用 read_file 读取这个文件核对内容。最后回复“材料已经生成”。请不要调用其他工具。`)
  await page.getByRole('button', { name: '发送任务', exact: true }).click()
  const fileConversation = await wait(() => page.evaluate(() => sessionStorage.getItem('conversation')), Boolean)
  const approval = page.locator('.attention-cards .approval')
  await approval.waitFor()
  assert.equal(await page.locator('.run-details').count(), 0)
  assert.ok((await approval.innerText()).includes(path))
  assert.equal(await page.getByRole('button', { name: '发送任务', exact: true }).isEnabled(), false)
  await approval.getByRole('button', { name: '仅本次允许', exact: true }).click()
  await approval.waitFor({ state: 'hidden' })
  await wait(() => call('GET', `/conversations/${fileConversation}/task-runs`), (runs) => runs.at(-1)?.status === 'completed')
  assert.equal((await readFile(path, 'utf8')).trim(), '界面真实产物检查')
  await proof.file(path)
  await page.locator('.artifact-card').filter({ hasText: '界面检查材料.md' }).waitFor()
  assert.equal(await page.getByRole('button', { name: '定位文件', exact: true }).isEnabled(), true)
  assert.equal(await page.locator('.execution-overview').count(), 1)
  await page.locator('.scope-picker > summary').click()
  assert.equal(await page.locator('.scope-picker').getAttribute('open'), '')
  await page.locator('.scope-picker > summary').press('Escape')
  assert.equal(await page.locator('.scope-picker').getAttribute('open'), null)
  await page.locator('.execution-overview > summary').click()
  assert.ok((await page.locator('.execution-overview').innerText()).includes('写入文件'))
  await page.getByRole('button', { name: '查看调用参数与结果', exact: true }).click()
  await page.locator('.run-details').waitFor()
  await page.locator('.tool-record > summary').first().click()
  assert.ok((await page.locator('.tool-record').first().innerText()).includes('write_file'))
  await page.getByRole('button', { name: '收起运行详情', exact: true }).last().click()

  for (const width of [1100, 960, 800]) {
    await page.setViewportSize({ width, height: 800 })
    assert.equal(await page.getByRole('button', { name: '发送任务', exact: true }).isVisible(), true)
    assert.equal(await page.getByRole('button', { name: '设置', exact: true }).isVisible(), true)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
    const bounds = await composer.boundingBox()
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width && bounds.y + bounds.height <= 800)
    await recordDesktopEvidence(page, `${directory}/workbench-${width}.json`)
  }
  await page.getByRole('button', { name: '展开运行详情', exact: true }).click()
  await page.locator('.run-details').waitFor()
  await page.keyboard.press('Escape')
  await page.locator('.run-details').waitFor({ state: 'hidden' })
  assert.equal(await page.getByRole('button', { name: '展开运行详情', exact: true }).evaluate((element) => element === document.activeElement), true)
  await page.getByRole('button', { name: '展开任务列表', exact: true }).click()
  await page.getByLabel('搜索任务', { exact: true }).waitFor()
  assert.equal(await page.locator('.navigation-expanded').count(), 1)
  await page.getByRole('button', { name: '收起任务列表', exact: true }).last().click()
  assert.deepEqual(errors, [])
  await app.evaluate(() => globalThis.agentcrewLifecycle.requestExit('menu'))
  await page.getByRole('dialog', { name: '退出 AgentCrew', exact: true }).waitFor()
  const closed = app.waitForEvent('close')
  await page.getByRole('button', { name: '确认退出', exact: true }).click()
  await closed
  await proof.save({ passed: true, cases: ['合并导航与任务列表', '新建任务与真实模型执行', '标题与中文状态', 'Markdown 表格与代码块', '真实剪贴板复制', '历史内容与最新内容导航', '任务搜索', '刷新恢复', '正文审批与发送阻断', '真实文件产物', '调用参数与结果', '1100/960/800 窗口布局', '键盘关闭与焦点恢复'], errors }, queries)
  console.log(`工作台真实交互检查通过：${directory}/result.json`)
} catch (error) {
  await proof.save({ passed: false, errors: [...errors, error.message] }, queries)
  throw error
} finally {
  if (child.exitCode === null) child.kill('SIGTERM')
}
