import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { cp, mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { _electron as electron } from 'playwright-core'

const directory = resolve('.artifacts', `m3-desktop-errors-${Date.now()}`)
await mkdir(`${directory}/data`, { recursive: true })
await cp('../backend/data/config.json', `${directory}/data/config.json`)
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`],
  executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const child = app.process()
const page = await app.firstWindow()
page.setDefaultTimeout(60000)
const errors = []
const traffic = []
const cases = []
let database
let locked = false
page.on('pageerror', (reason) => errors.push(reason.message))
page.on('response', (response) => {
  const path = new URL(response.url()).pathname
  if (path.startsWith('/api/notifications') || path === '/api/settings/desktop') {
    traffic.push({ method: response.request().method(), path, status: response.status(), at: Date.now() })
  }
})
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
const lock = () => { database.exec('BEGIN IMMEDIATE'); locked = true }
const unlock = () => { database.exec('ROLLBACK'); locked = false }
const wait = async (read, accept) => {
  const deadline = Date.now() + 60000
  while (Date.now() < deadline) {
    const value = await read()
    if (accept(value)) return value
    await new Promise((done) => setTimeout(done, 100))
  }
  throw new Error('真实桌面状态未在期限内达到检查条件')
}

try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  await page.getByRole('button', { name: '设置', exact: true }).click()
  const save = page.getByRole('button', { name: '保存桌面设置', exact: true })
  await save.waitFor()
  database = new DatabaseSync(`${directory}/data/agentcrew.db`)
  await page.getByRole('switch', { name: '关闭窗口时退出应用', exact: true }).click()
  lock()
  await save.click()
  const settingsError = page.locator('.desktop-settings [role="alert"]')
  await settingsError.waitFor()
  const saveReason = await settingsError.innerText()
  assert.ok(saveReason.length > 0)
  assert.equal(saveReason.includes('请检查服务连接'), false)
  assert.equal(await save.isEnabled(), true)
  assert.equal(await page.getByText('桌面设置已保存。', { exact: true }).count(), 0)
  assert.deepEqual(errors, [])
  unlock()
  await save.click()
  await page.getByText('桌面设置已保存。', { exact: true }).waitFor()
  assert.equal(await settingsError.count(), 0)
  assert.equal((await call('GET', '/settings/desktop')).close_to_quit, true)
  cases.push({ name: '真实 SQLite 写锁导致保存失败，显示原始错误并允许重新保存', reason: saveReason })

  // 暂停消费后创建真实错过计划，确保写锁阻挡 presented 请求。
  await app.evaluate(() => globalThis.agentcrewLifecycle.requestExit('menu'))
  await page.getByRole('dialog', { name: '退出 AgentCrew', exact: true }).waitFor()
  const job = await call('POST', '/cron/jobs', {
    change_id: randomUUID(), workspace_id: 'office', agent_id: 'xiaowen', name: '桌面错误恢复真实检查',
    schedule: { kind: 'at', at_ms: Date.now() - 60000, tz: 'Asia/Shanghai' },
    target: { instruction: '仅回复收到。', execution_mode: 'new_conversation', conversation_id: null }, pre_authorized: []
  })
  const notification = await wait(() => call('GET', '/notifications?limit=200'),
    (value) => value.items.some((item) => item.job_id === job.id))
  const target = notification.items.find((item) => item.job_id === job.id)
  assert.equal(target.presented_at, null)
  lock()
  await page.getByRole('button', { name: '取消退出', exact: true }).click()
  await page.getByRole('button', { name: /^通知中心，/ }).click()
  const notificationError = page.locator('.notification-center [role="alert"]')
  await notificationError.waitFor()
  const pollingReason = await notificationError.innerText()
  assert.ok(pollingReason.length > 0)
  assert.deepEqual(errors, [])
  unlock()
  const resumedAt = Date.now()
  await page.locator('.notification-list > li').filter({ hasText: job.name }).waitFor()
  await wait(() => traffic, (values) => values.some((entry) => entry.at >= resumedAt && entry.method === 'GET'
    && entry.path === '/api/notifications' && entry.status === 200))
  await notificationError.waitFor({ state: 'hidden' })
  cases.push({ name: 'presented 请求真实失败后自动轮询恢复，没有未处理 rejection', reason: pollingReason })

  lock()
  await page.locator('.notification-list > li').filter({ hasText: job.name }).getByRole('button', { name: '标记已读', exact: true }).click()
  await notificationError.waitFor()
  const readReason = await notificationError.innerText()
  assert.ok(readReason.length > 0)
  assert.deepEqual(errors, [])
  unlock()
  await wait(() => call('GET', '/notifications?limit=200'),
    (value) => value.items.find((item) => item.id === target.id)?.read_at != null)
  cases.push({ name: '标记已读真实请求失败显示原因，恢复后读取服务器最终状态', reason: readReason })

  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByLabel('演示身份', { exact: true }).selectOption('lilei')
  await page.getByRole('button', { name: /^通知中心，/ }).click()
  await page.getByText('当前没有通知。', { exact: true }).waitFor()
  assert.equal(await page.locator('.notification-list > li').count(), 0)
  assert.equal(await notificationError.count(), 0)
  assert.equal(await page.getByRole('button', { name: '通知中心，0 条未读通知', exact: true }).count(), 1)
  cases.push({ name: '身份切换清理旧通知与请求错误' })
  assert.deepEqual(errors, [])
  await app.evaluate(() => globalThis.agentcrewLifecycle.requestExit('menu'))
  await page.getByRole('dialog', { name: '退出 AgentCrew', exact: true }).waitFor()
  const closed = app.waitForEvent('close')
  await page.getByRole('button', { name: '确认退出', exact: true }).click()
  await closed
  await writeFile(`${directory}/result.json`, JSON.stringify({ passed: true, cases, errors, traffic }, null, 2))
  console.log(`桌面真实错误恢复检查通过：${directory}/result.json`)
} finally {
  if (locked) unlock()
  database?.close()
  if (child.exitCode === null) child.kill('SIGTERM')
}
