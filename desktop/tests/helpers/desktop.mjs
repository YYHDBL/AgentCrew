import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { writeFile } from 'node:fs/promises'
import { setTimeout as delay } from 'node:timers/promises'

const memoryLabels = ['USER 记忆', '工作区记忆', '员工 soul', '技能管理']
const settingsTabs = { settings: '桌面行为', studio: '数字员工', admin: '组织管理', governance: '权限与审计' }
const closedApplications = new WeakSet()

async function navigation(page) {
  const expand = page.getByRole('button', { name: '展开侧栏', exact: true })
  if (await expand.isVisible()) await expand.click()
}

export async function openSettings(page, tab = '桌面行为') {
  assert.ok(Object.values(settingsTabs).includes(tab), `未知设置页面：${tab}`)
  await navigation(page)
  await page.getByRole('button', { name: '设置', exact: true }).click()
  const target = page.locator('.settings-shell > .ant-tabs > .ant-tabs-nav').getByRole('tab', { name: new RegExp(`${tab}$`) })
  await target.click()
  await target.waitFor({ state: 'visible' })
  assert.equal(await target.getAttribute('aria-selected'), 'true')
}

export async function openMemory(page, label) {
  assert.ok(memoryLabels.includes(label), `未知记忆页面：${label}`)
  await navigation(page)
  await page.getByRole('button', { name: '记忆与技能', exact: true }).click()
  await page.getByRole('menuitem', { name: label, exact: true }).click()
  await page.getByRole('heading', { name: label, exact: true }).waitFor()
}

export async function openConversation(page, conversationId) {
  await navigation(page)
  await page.getByRole('button', { name: '工作台', exact: true }).click()
  const expand = page.getByRole('button', { name: '展开任务列表', exact: true })
  if (await expand.isVisible()) await expand.click()
  await page.getByRole('textbox', { name: '搜索任务', exact: true }).fill('')
  assert.ok(/^[a-zA-Z0-9-]+$/.test(conversationId), '任务标识包含无效字符')
  await page.locator(`.recent-tasks button[data-conversation-id="${conversationId}"]`).click()
  await page.waitForFunction((id) => sessionStorage.getItem('conversation') === id, conversationId)
}

export async function openRunDetails(page) {
  const expand = page.getByRole('button', { name: '展开运行详情', exact: true })
  if (await expand.isVisible()) await expand.click()
  await page.getByRole('complementary', { name: '运行详情', exact: true }).waitFor()
}

export async function changeIdentity(page, user, { restore = true } = {}) {
  const previous = await page.evaluate(() => sessionStorage.getItem('memory-page') ?? 'tasks')
  await openSettings(page)
  await page.getByLabel('演示身份', { exact: true }).selectOption(user)
  await page.waitForFunction(async (expected) => {
    const port = await window.agentcrew.getBackendPort()
    const token = await window.agentcrew.getToken()
    if (port === null || token === null) return false
    if (!Number.isInteger(port) || port <= 0 || typeof token !== 'string' || token.length === 0) throw new Error('身份核查所需的服务连接信息无效')
    const identity = sessionStorage.getItem('agentcrew-identity')
    const response = await fetch(`http://127.0.0.1:${port}/api/identity`, {
      headers: { Authorization: `Bearer ${token}`, ...(identity ? { 'X-AgentCrew-Identity': identity } : {}) }
    })
    if (!response.ok) throw new Error(`身份核查失败：HTTP ${response.status}`)
    const actual = (await response.json()).data
    return actual.effective_user_id === expected && document.querySelector('[aria-label="演示身份"]')?.value === expected
  }, user)
  if (!restore) return
  if (previous in settingsTabs) await openSettings(page, settingsTabs[previous])
  else if (memoryLabels.includes(previous)) await openMemory(page, previous)
  else if (['user', 'workspace', 'soul', 'skill'].includes(previous)) await openMemory(page, memoryLabels[['user', 'workspace', 'soul', 'skill'].indexOf(previous)])
  else {
    await navigation(page)
    if (previous === 'notifications') await page.getByRole('button', { name: /^通知中心，/ }).click()
    else await page.getByRole('button', { name: { tasks: '工作台', runs: '运行中心', automation: '自动化' }[previous], exact: true }).click()
  }
}

export async function recordDesktopEvidence(page, path) {
  const evidence = await page.evaluate(() => ({
    viewport: { width: innerWidth, height: innerHeight },
    document: { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight },
    text: document.body.innerText,
    focus: document.activeElement?.getAttribute('aria-label') ?? document.activeElement?.textContent,
    controls: [...document.querySelectorAll('button, input, select, textarea, [role="tab"]')].map((element) => ({
      role: element.getAttribute('role') ?? element.tagName.toLowerCase(),
      name: element.getAttribute('aria-label') ?? element.textContent?.trim(),
      disabled: element.disabled ?? element.getAttribute('aria-disabled') === 'true',
      bounds: element.getBoundingClientRect().toJSON()
    }))
  }))
  await writeFile(path, JSON.stringify(evidence, null, 2))
}

export async function closeDesktop(app, page) {
  if (closedApplications.has(app) || (page.isClosed() && app.windows().length === 0)) return
  const child = app.process()
  assert.ok(child?.pid, '无法取得本次测试的 Electron 进程')
  if (child.exitCode !== null || child.signalCode !== null) return
  const processes = execFileSync('ps', ['-axo', 'pid,ppid'], { encoding: 'utf8' }).trim().split('\n').slice(1)
    .map((line) => line.trim().split(/\s+/).map(Number))
  const descendants = new Set([child.pid])
  for (let size = 0; size !== descendants.size;) {
    size = descendants.size
    for (const [pid, parent] of processes) if (descendants.has(parent)) descendants.add(pid)
  }
  let closed = false
  try {
    await app.evaluate(({ app }) => app.quit())
    await page.getByRole('button', { name: '确认退出', exact: true }).waitFor({ timeout: 20000 })
    await Promise.all([
      app.waitForEvent('close', { timeout: 30000 }),
      page.getByRole('button', { name: '确认退出', exact: true }).click({ timeout: 10000 })
    ])
    closed = true
    closedApplications.add(app)
  } finally {
    if (!closed) {
      const current = execFileSync('ps', ['-axo', 'pid,ppid'], { encoding: 'utf8' }).trim().split('\n').slice(1)
        .map((line) => line.trim().split(/\s+/).map(Number))
      for (const [pid, parent] of current) if (descendants.has(pid) && (pid === child.pid || descendants.has(parent))) process.kill(pid, 'SIGTERM')
      const deadline = Date.now() + 10000
      while (child.exitCode === null && child.signalCode === null && Date.now() < deadline) await delay(50)
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL')
    }
  }
}
