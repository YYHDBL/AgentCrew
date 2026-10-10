import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { execFileSync, spawnSync } from 'node:child_process'
import { cp, mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { _electron as electron } from 'playwright-core'
import { evidence } from './helpers/evidence.mjs'
import { recordDesktopEvidence } from './helpers/desktop.mjs'

const directory = resolve('.artifacts', `desktop-lifecycle-${Date.now()}`)
await mkdir(`${directory}/data`, { recursive: true })
await cp('../backend/data/config.json', `${directory}/data/config.json`)
const proof = await evidence(directory)
let app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`], executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
let child = app.process()
let page = await app.firstWindow()
proof.attach(page)
page.setDefaultTimeout(30000)
const errors = []
const queries = ['SELECT * FROM runtime_notifications ORDER BY created_at',
  'SELECT id,name,schedule,target,enabled,next_run_at,last_status,metadata FROM cron_jobs',
  'SELECT * FROM cron_job_runs ORDER BY created_at',
  'SELECT id,conversation_id,status,current_attempt_no FROM task_runs',
  'SELECT global_seq,task_run_id,seq,type,payload FROM run_events ORDER BY global_seq',
  'SELECT seq,action,resource_type,resource_id,hash FROM audit_log ORDER BY seq']
let pausedPid = null
page.on('pageerror', (error) => errors.push(error.message))

const call = (method, path, body) => page.evaluate(async ({ method, path, body }) => {
  const port = await window.agentcrew.getBackendPort()
  const token = await window.agentcrew.getToken()
  if (!port || !token) throw new Error('真实任务服务尚未就绪')
  const response = await fetch(`http://127.0.0.1:${port}/api${path}`, {
    method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  const envelope = await response.json()
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${envelope.error?.message}`)
  return envelope.data
}, { method, path, body })

const wait = async (read, accept, timeout = 30000) => {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const value = await read()
    if (accept(value)) return value
    await new Promise((done) => setTimeout(done, 100))
  }
  throw new Error('真实桌面状态未在期限内达到检查条件')
}

const shown = () => app.evaluate(() => globalThis.agentcrewLifecycle.shownNotifications().map((entry) => entry.detail))
const changeIdentity = async (user) => {
  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByLabel('演示身份', { exact: true }).selectOption(user)
  await page.waitForFunction((value) => document.querySelector('.identity-control select')?.value === value, user)
}

try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByRole('switch', { name: '关闭窗口时退出应用', exact: true }).waitFor()
  const exposed = await page.evaluate(() => Object.keys(window.agentcrew))
  assert.equal(exposed.includes('notify'), false)
  assert.equal(exposed.includes('setKeepAwake'), false)
  const info = await page.evaluate(() => window.agentcrew.getDesktopInfo())
  assert.equal(info.dataDir, `${directory}/data`)
  assert.ok(await page.locator('.desktop-settings').innerText().then((text) => text.includes(info.dataDir) && text.includes('不会补跑')))
  assert.equal(await page.locator('.titlebar').getByLabel('演示身份', { exact: true }).count(), 0)
  for (const name of ['数字员工', '组织管理', '权限与审计', '桌面行为']) {
    await page.getByRole('tab', { name: new RegExp(name) }).click()
    assert.equal(await page.getByRole('tab', { name: new RegExp(name) }).getAttribute('aria-selected'), 'true')
  }
  await page.setViewportSize({ width: 800, height: 760 })
  assert.equal(await page.getByRole('button', { name: '设置', exact: true }).isVisible(), true)
  assert.equal(await page.getByRole('button', { name: '自动化', exact: true }).isVisible(), true)
  assert.equal(await page.getByRole('button', { name: /^通知中心，/ }).isVisible(), true)
  assert.equal(await page.locator('.navigation .nav-label').first().isVisible(), false)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('switch', { name: '关闭窗口时退出应用', exact: true }).click()
  await page.getByRole('switch', { name: '运行期间防止系统自动休眠', exact: true }).click()
  await page.getByRole('button', { name: '保存桌面设置', exact: true }).click()
  await page.getByText('桌面设置已保存。', { exact: true }).waitFor()
  assert.deepEqual(await call('GET', '/settings/desktop'), { close_to_quit: true, prevent_sleep: false })
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close())
  const closeDialog = page.getByRole('dialog', { name: '退出 AgentCrew', exact: true })
  await closeDialog.waitFor()
  assert.ok((await closeDialog.innerText()).includes('关闭窗口'))
  await page.getByRole('button', { name: '取消退出', exact: true }).click()
  await closeDialog.waitFor({ state: 'hidden' })
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), false)
  execFileSync(resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'), ['.', `--user-data-dir=${directory}`], { timeout: 10000, stdio: 'ignore' })
  await wait(() => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), Boolean)
  await page.reload()
  await page.getByRole('switch', { name: '关闭窗口时退出应用', exact: true }).waitFor()
  assert.equal(await page.getByRole('switch', { name: '关闭窗口时退出应用', exact: true }).getAttribute('aria-checked'), 'true')
  assert.equal(await page.getByRole('switch', { name: '运行期间防止系统自动休眠', exact: true }).getAttribute('aria-checked'), 'false')

  await changeIdentity('lilei')
  await page.getByRole('switch', { name: '关闭窗口时退出应用', exact: true }).waitFor()
  assert.equal(await page.getByRole('switch', { name: '关闭窗口时退出应用', exact: true }).isEnabled(), false)
  assert.equal(await page.getByRole('button', { name: '保存桌面设置', exact: true }).isEnabled(), false)

  const jobs = []
  for (let index = 0; index < 21; index += 1) {
    jobs.push(await call('POST', '/cron/jobs', {
      change_id: randomUUID(), workspace_id: 'office', agent_id: 'xiaowen', name: `桌面通知真实检查 ${index + 1}`,
      schedule: { kind: 'at', at_ms: Date.now() - 60000, tz: 'Asia/Shanghai' },
      target: { instruction: '仅回复收到。', execution_mode: 'new_conversation', conversation_id: null }, pre_authorized: []
    }))
  }
  const records = await wait(() => call('GET', '/notifications?limit=200'), (value) => value.items.length === jobs.length)
  for (const notification of records.items.slice(1)) {
    const marked = await call('PATCH', `/notifications/${notification.id}`, { action: 'presented' })
    assert.equal(marked.claimed, true)
  }
  const target = records.items[0]
  const next = await call('GET', '/notifications?limit=1')
  assert.ok(next.next_after)
  assert.equal((await call('GET', `/notifications?limit=1&after=${encodeURIComponent(next.next_after)}`)).items.length, 1)
  await page.getByRole('button', { name: /^通知中心，/ }).click()
  await page.getByText('当前没有通知。', { exact: true }).waitFor()
  assert.equal((await shown()).length, 0)
  await changeIdentity('owner')
  await page.getByRole('button', { name: /^通知中心，/ }).click()
  await page.locator('.notification-list > li').first().waitFor()
  await wait(shown, (value) => value.length === 1)
  assert.deepEqual((await shown())[0], { id: target.id, job_id: target.job_id, task_run_id: null })
  assert.equal(await page.locator('.notification-list > li').count(), 20)
  await page.locator('.ant-pagination-item-2').click()
  assert.equal(await page.locator('.notification-list > li').count(), 1)
  await page.locator('.ant-pagination-item-1').click()
  await page.locator('.notification-list > li').first().getByRole('button', { name: '标记已读', exact: true }).click()
  await wait(() => call('GET', '/notifications?limit=200'), (value) => value.unread_count === jobs.length - 1)
  await page.getByRole('button', { name: '刷新通知', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.title-actions button[aria-label^="通知中心"]')?.getAttribute('aria-label') === '通知中心，20 条未读通知')
  await recordDesktopEvidence(page, `${directory}/notifications.json`)
  assert.equal((await shown()).length, 1)
  await page.locator('.notification-list > li').first().getByRole('button', { name: '查看关联计划', exact: true }).click()
  await page.getByRole('heading', { name: '自动化', exact: true }).waitFor()
  await page.waitForFunction((id) => document.activeElement?.id === `cron-job-${id}`, target.job_id)

  await page.getByRole('button', { name: /^通知中心，/ }).click()
  await changeIdentity('lilei')
  await page.getByRole('button', { name: /^通知中心，/ }).click()
  await page.getByText('当前没有通知。', { exact: true }).waitFor()
  assert.equal(await page.locator('.notification-list > li').count(), 0)
  assert.equal(await page.getByRole('button', { name: '通知中心，0 条未读通知', exact: true }).count(), 1)
  await changeIdentity('owner')
  await page.getByRole('button', { name: /^通知中心，/ }).click()
  await page.locator('.notification-list > li').first().waitFor()

  const source = new DatabaseSync(resolve('../data/m3-intermediate/02-api-acceptance/identity-http0/agentcrew.db'), { readOnly: true })
  const soul = source.prepare("SELECT id,model,task_run_id,result FROM memory_soul_generations WHERE status='completed' ORDER BY created_at LIMIT 1").get()
  source.close()
  assert.ok(soul?.result, '缺少真实模型生成的岗位记忆记录')
  await call('POST', '/memory/stores/soul/xiaowen?workspace_id=office&agent_id=xiaowen', {
    change_id: randomUUID(), expected_revision: 0, text: soul.result,
    basis: `桌面检查使用真实生成记录 ${soul.id}（${soul.model}，TaskRun ${soul.task_run_id}）初始化独立员工。`
  })
  const completedJob = await call('POST', '/cron/jobs', {
    change_id: randomUUID(), workspace_id: 'office', agent_id: 'xiaowen', name: '桌面成功通知真实检查',
    schedule: { kind: 'at', at_ms: Date.now() + 3600000, tz: 'Asia/Shanghai' },
    target: { instruction: '仅回复收到，直接结束任务。', execution_mode: 'new_conversation', conversation_id: null }, pre_authorized: []
  })
  await call('POST', `/cron/jobs/${completedJob.id}/run-now`, { client_request_id: randomUUID(), expected_revision: completedJob.revision })
  const completedNotifications = await wait(() => call('GET', '/notifications?limit=200'),
    (value) => value.items.some((notification) => notification.job_id === completedJob.id), 120000)
  const badge = completedNotifications.items.find((notification) => notification.job_id === completedJob.id)
  assert.equal(badge.severity, 'badge')
  assert.equal(badge.presented_at, null)
  await page.getByRole('button', { name: '刷新通知', exact: true }).click()
  await page.locator('.notification-list > li').filter({ hasText: completedJob.name }).waitFor()
  assert.equal((await shown()).length, 1)
  const completedItem = page.locator('.notification-list > li').filter({ hasText: completedJob.name })
  await completedItem.getByRole('button', { name: '查看运行记录', exact: true }).click()
  await page.getByRole('heading', { name: '运行中心', exact: true }).waitFor()
  await page.waitForFunction((id) => document.querySelector('.run-record')?.textContent?.includes(id), badge.task_run_id)
  await page.reload()
  await page.getByRole('button', { name: /^通知中心，/ }).click()
  await page.locator('.notification-list > li').first().waitFor()
  assert.equal((await shown()).length, 1)
  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByRole('switch', { name: '关闭窗口时退出应用', exact: true }).click()
  await page.getByRole('switch', { name: '运行期间防止系统自动休眠', exact: true }).click()
  await page.getByRole('button', { name: '保存桌面设置', exact: true }).click()
  await page.getByText('桌面设置已保存。', { exact: true }).waitFor()
  assert.deepEqual(await call('GET', '/settings/desktop'), { close_to_quit: false, prevent_sleep: true })

  const runsBeforeRestart = await call('GET', '/task-runs?limit=200')
  await page.evaluate(() => window.agentcrew.restartBackend())
  const afterRestart = await call('GET', '/notifications?limit=200')
  assert.equal(afterRestart.items.length, completedNotifications.items.length)
  assert.equal(afterRestart.items.find((notification) => notification.id === target.id).read_at !== null, true)
  assert.equal((await call('GET', '/task-runs?limit=200')).items.length, runsBeforeRestart.items.length)
  await page.getByRole('button', { name: /^通知中心，/ }).click()
  await page.locator('.notification-list > li').first().waitFor()
  assert.equal((await shown()).length, 1)

  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close())
  await wait(() => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), (visible) => !visible)
  const hiddenJob = await call('POST', '/cron/jobs', {
    change_id: randomUUID(), workspace_id: 'office', agent_id: 'xiaowen', name: '托盘驻留调度真实检查',
    schedule: { kind: 'at', at_ms: Date.now() + 2000, tz: 'Asia/Shanghai' },
    target: { instruction: '仅回复收到，直接结束任务。', execution_mode: 'new_conversation', conversation_id: null }, pre_authorized: []
  })
  await wait(() => call('GET', `/cron/jobs/${hiddenJob.id}/runs?limit=200`),
    (value) => value.items.some((occurrence) => occurrence.status === 'completed'), 120000)
  const hiddenNotification = await wait(() => call('GET', '/notifications?limit=200'),
    (value) => value.items.some((notification) => notification.job_id === hiddenJob.id))
  assert.equal(hiddenNotification.items.find((notification) => notification.job_id === hiddenJob.id).severity, 'badge')
  assert.equal(await app.evaluate(() => globalThis.agentcrewLifecycle.tray().isDestroyed()), false)
  execFileSync(resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'), ['.', `--user-data-dir=${directory}`], { timeout: 10000, stdio: 'ignore' })
  await wait(() => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), Boolean)
  await page.getByRole('button', { name: '刷新通知', exact: true }).click()
  await page.locator('.notification-list > li').filter({ hasText: hiddenJob.name }).waitFor()
  assert.equal((await shown()).length, 1)

  const supervisorPid = Number(execFileSync('pgrep', ['-P', String(child.pid), '-f', 'uv run python -m agentcrew_server']).toString().trim())
  const backendPid = Number(execFileSync('pgrep', ['-P', String(supervisorPid)]).toString().trim())
  assert.ok(Number.isSafeInteger(supervisorPid) && supervisorPid > 0 && Number.isSafeInteger(backendPid) && backendPid > 0)
  const pausedJob = await call('POST', '/cron/jobs', {
    change_id: randomUUID(), workspace_id: 'office', agent_id: 'xiaowen', name: '进程暂停后错过计划真实检查',
    schedule: { kind: 'at', at_ms: Date.now() + 2000, tz: 'Asia/Shanghai' },
    target: { instruction: '仅回复收到。', execution_mode: 'new_conversation', conversation_id: null }, pre_authorized: []
  })
  process.kill(backendPid, 'SIGSTOP')
  pausedPid = backendPid
  await new Promise((done) => setTimeout(done, 6000))
  process.kill(backendPid, 'SIGCONT')
  pausedPid = null
  const pausedRuns = await wait(() => call('GET', `/cron/jobs/${pausedJob.id}/runs?limit=200`), (value) => value.items.length > 0)
  assert.equal(pausedRuns.items.length, 1)
  assert.equal(pausedRuns.items[0].status, 'missed')
  assert.equal(pausedRuns.items[0].task_run_id, null)
  await wait(shown, (value) => value.length === 2)

  await app.evaluate(() => globalThis.agentcrewLifecycle.requestExit('menu'))
  const dialog = page.getByRole('dialog', { name: '退出 AgentCrew', exact: true })
  await dialog.waitFor()
  assert.ok((await dialog.innerText()).includes('未来 24 小时计划'))
  await page.waitForFunction(() => document.activeElement?.textContent === '取消退出')
  await page.keyboard.press('Escape')
  await dialog.waitFor({ state: 'hidden' })
  await app.evaluate(() => globalThis.agentcrewLifecycle.requestExit('tray'))
  await dialog.waitFor()
  assert.ok((await dialog.innerText()).includes('系统托盘'))
  await page.getByRole('button', { name: '取消退出', exact: true }).click()
  await dialog.waitFor({ state: 'hidden' })
  assert.deepEqual(errors, [])
  const systemNotifications = (await shown()).length
  await recordDesktopEvidence(page, `${directory}/lifecycle.json`)
  const offlineJob = await call('POST', '/cron/jobs', {
    change_id: randomUUID(), workspace_id: 'office', agent_id: 'xiaowen', name: '退出期间错过计划真实检查',
    schedule: { kind: 'at', at_ms: Date.now() + 8000, tz: 'Asia/Shanghai' },
    target: { instruction: '仅回复收到。', execution_mode: 'new_conversation', conversation_id: null }, pre_authorized: []
  })
  await app.evaluate(() => globalThis.agentcrewLifecycle.requestExit('menu'))
  await dialog.waitFor()
  const closed = app.waitForEvent('close')
  await page.getByRole('button', { name: '确认退出', exact: true }).click()
  await closed
  assert.equal(spawnSync('kill', ['-0', String(backendPid)], { stdio: 'ignore' }).status, 1)
  assert.equal(spawnSync('kill', ['-0', String(supervisorPid)], { stdio: 'ignore' }).status, 1)
  const remaining = offlineJob.schedule.at_ms + 2500 - Date.now()
  assert.ok(remaining > 0, '应用必须在计划到期之前真正退出')
  await new Promise((done) => setTimeout(done, remaining))
  app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`], executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
  child = app.process()
  page = await app.firstWindow()
  proof.attach(page)
  page.setDefaultTimeout(30000)
  page.on('pageerror', (error) => errors.push(error.message))
  await page.getByRole('button', { name: '工作台', exact: true }).click()
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  const offlineRuns = await call('GET', `/cron/jobs/${offlineJob.id}/runs?limit=200`)
  assert.equal(offlineRuns.items.length, 1)
  assert.equal(offlineRuns.items[0].status, 'missed')
  assert.equal(offlineRuns.items[0].task_run_id, null)
  const persisted = await call('GET', '/notifications?limit=200')
  assert.equal(persisted.items.find((notification) => notification.id === target.id).read_at !== null, true)
  await wait(shown, (value) => value.length === 1)
  assert.equal((await shown())[0].job_id, offlineJob.id)
  await app.evaluate(() => globalThis.agentcrewLifecycle.requestExit('menu'))
  const restartedDialog = page.getByRole('dialog', { name: '退出 AgentCrew', exact: true })
  await restartedDialog.waitFor()
  const restartedClosed = app.waitForEvent('close')
  await page.getByRole('button', { name: '确认退出', exact: true }).click()
  await restartedClosed
  assert.deepEqual(errors, [])
  await proof.save({ passed: true, notifications: persisted.items.length, system_notifications: systemNotifications + 1, cases: ['窄 IPC', '设置分页导航', '窄窗口图标入口', '设置保存与重载', '关窗退出设置触发真实确认', '成员只读', '后端通知分页', '通知中心分页', '系统提醒认领去重', '通知已读与未读计数', '关联计划导航', '身份切换缓存清理', '成功通知仅在应用内展示', '运行记录导航', '重载后通知去重', 'sidecar 重启后状态与调度去重', '关闭窗口驻留托盘', '隐藏窗口期间计划真实执行', '重新激活显示窗口', 'SIGSTOP/SIGCONT 错过登记且不补跑', '退出快照', '取消与键盘焦点', '确认退出', '真正退出后无后端残留进程', '应用重新启动登记错过且不补跑', '重新启动保持未读状态与提醒去重'], errors }, queries)
  console.log(`桌面真实集成检查通过：${directory}/result.json`)
} catch (error) {
  await proof.save({ passed: false, errors: [...errors, error.message] }, queries)
  throw error
} finally {
  if (pausedPid !== null) process.kill(pausedPid, 'SIGCONT')
  if (child.exitCode === null) child.kill('SIGTERM')
}
