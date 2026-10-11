import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { cp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { _electron as electron } from 'playwright-core'
import { openSettings, changeIdentity } from './helpers/desktop.mjs'

// M4-04：首次使用与模型配置。独立 userData、不拷贝开发 config，从界面
// 输入真实凭据完成新会话（write_file 审批-写入）与 aux 作业；再覆盖无效
// 密钥、不可达地址、保存失败草稿保留、成员只读、清除/更新密钥、重启
// 持久化、缺计数资源与 env 覆盖提示。真实 Electron + 真实打包 sidecar。

const BINARY = resolve('resources/sidecar/darwin-arm64/agentcrew-sidecar')
const EXECUTABLE = resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron')
const directory = resolve('.artifacts', `m4-onboarding-${Date.now()}`)
const results = []
const record = (name, passed, detail = '') => {
  results.push({ check: name, result: passed ? 'passed' : 'failed', detail: String(detail).slice(0, 600) })
  console.log(`${passed ? '✓' : '✗'} ${name}${passed ? '' : '：' + detail}`)
}
const waitFor = async (read, accept, timeout = 45000) => {
  const deadline = Date.now() + timeout
  let value
  while (Date.now() < deadline) {
    value = await read()
    if (accept(value)) return value
    await delay(150)
  }
  throw new Error(`真实状态未在期限内满足：${JSON.stringify(value)?.slice(0, 300)}`)
}
const launchApp = (sessionDir, env = {}) => electron.launch({
  args: ['.', `--user-data-dir=${sessionDir}`], executablePath: EXECUTABLE,
  env: { ...process.env, AGENTCREW_SIDECAR_BINARY: BINARY, ...env }
})
const exitOf = (childProcess, timeout = 30000) => new Promise((done) => {
  const timer = setTimeout(() => done('timeout'), timeout)
  childProcess.once('exit', (code) => { clearTimeout(timer); done(code) })
})
const request = (page, method, path, body) => page.evaluate(async ({ method, path, body }) => {
  const port = await window.agentcrew.getBackendPort()
  const token = await window.agentcrew.getToken()
  const response = await fetch(`http://127.0.0.1:${port}/api${path}`, {
    method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  const envelope = await response.json()
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${envelope.error?.message}`)
  return envelope.data
}, { method, path, body })

const realKey = JSON.parse(await readFile('../backend/data/config.json', 'utf8')).models.main.api_key
assert.ok(realKey, '缺少真实模型密钥（backend/data/config.json）')
let app = null
const consoleLog = []
const attachConsole = (page) => {
  page.on('console', (message) => consoleLog.push(`[${message.type()}] ${message.text().slice(0, 220)}`))
  page.on('pageerror', (error) => consoleLog.push(`[pageerror] ${error.message.slice(0, 220)}`))
}

try {
  await mkdir(`${directory}/main/data`, { recursive: true })
  const sessionDir = resolve(directory, 'main')

  // ── A 全新空配置启动：界面可用、工作台说明需要配置 ──
  {
    app = await launchApp(sessionDir)
    const page = await app.firstWindow()
    attachConsole(page)
    page.setDefaultTimeout(45000)
    await page.getByText('任务服务已连接', { exact: true }).waitFor()
    await page.getByText('模型连接尚未配置，任务暂时无法执行。').waitFor()
    await openSettings(page, '模型连接')
    await page.getByText('密钥未配置').first().waitFor()
    record('空配置启动：设置可用且提示未配置', true)
  }
  const page = await app.firstWindow()

  // ── B 从界面输入真实凭据并保存（密钥不回填 DOM） ──
  {
    await page.getByRole('button', { name: '用于主模型', exact: true }).first().click()
    await page.getByRole('button', { name: '用于辅助模型', exact: true }).first().click()
    await page.getByLabel('main API 密钥', { exact: true }).fill(realKey)
    await page.getByLabel('aux API 密钥', { exact: true }).fill(realKey)
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await page.getByText('配置已保存', { exact: false }).first().waitFor()
    const keyState = await page.locator('.model-key-status').allInnerTexts()
    record('界面保存后显示已配置（脱敏尾 4 位）',
      keyState.filter((text) => text.includes('密钥已配置')).length === 2, JSON.stringify(keyState))
    const bodyText = await page.evaluate(() => document.body.innerText + document.body.innerHTML)
    record('真实密钥不出现在 DOM', !bodyText.includes(realKey))
    const view = await request(page, 'GET', '/settings')
    record('GET 仅返回脱敏状态', !JSON.stringify(view).includes(realKey)
      && view.models.main?.api_key_configured === true)
  }

  // ── C 验证连接：计数资产 + 真实请求（端点限流时按真实行为重试一次） ──
  {
    let outcomeText = ''
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      await page.getByRole('button', { name: '验证连接', exact: true }).click()
      await page.getByText('连接验证结果').waitFor()
      outcomeText = await waitFor(async () => (await page.locator('.model-verify-outcome').allInnerTexts()).join('\n'),
        (text) => (text.match(/连接请求：(通过|未通过)/g) ?? []).length >= 2, 90000)
      const passed = (outcomeText.match(/连接请求：通过/g) ?? []).length
      const limited = /RATE_LIMIT|429|限流/.test(outcomeText)
      if (passed >= 2 || !limited) break
      await delay(12000)
    }
    record('验证连接：两槽计数与真实请求均通过',
      (outcomeText.match(/连接请求：通过/g) ?? []).length >= 2
      && (outcomeText.match(/计数资产：通过/g) ?? []).length >= 2,
      outcomeText.slice(0, 400))
  }

  // ── D 真实任务：write_file 审批-写入-读取（含 aux soul/标题作业） ──
  {
    await page.getByRole('button', { name: '设置', exact: true }).click()
    await page.getByRole('button', { name: '工作台', exact: true }).click()
    await waitFor(() => page.locator('.model-setup-hint').count(), (count) => count === 0)
    const target = `${sessionDir}/data/workspaces/default/m4-onboarding.txt`
    await page.getByRole('textbox', { name: '任务指令', exact: true }).fill(
      `请调用 write_file 把一行文字 “onboarding ${Date.now()}” 写入文件 ${target}，再用 read_file 读取该文件确认内容一致，然后回复“已完成”。不要做其他事。`)
    await page.getByRole('button', { name: '发送任务', exact: true }).click()
    await page.getByRole('button', { name: '仅本次允许', exact: true }).first().waitFor({ timeout: 120000 })
    await page.getByRole('button', { name: '仅本次允许', exact: true }).first().click()
    const conversation = await waitFor(() => page.evaluate(() => sessionStorage.getItem('conversation')), Boolean)
    await waitFor(() => request(page, 'GET', `/conversations/${conversation}/task-runs`),
      (runs) => runs.at(-1)?.status === 'completed', 300000)
    const content = await readFile(target, 'utf8')
    record('真实任务完成：审批后文件写入并读取', content.includes('onboarding'), content.slice(0, 80))
    const runs = await request(page, 'GET', `/conversations/${conversation}/task-runs`)
    record('任务终态 completed', runs.at(-1).status === 'completed', runs.at(-1).status)
  }

  // ── E 无效密钥：验证显示鉴权失败 ──
  {
    await openSettings(page, '模型连接')
    await page.getByLabel('main API 密钥', { exact: true }).fill('sk-invalid-onboarding-probe-000')
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await page.getByText('配置已保存', { exact: false }).first().waitFor()
    await page.getByRole('button', { name: '验证连接', exact: true }).click()
    const failed = await waitFor(async () => (await page.locator('.model-verify-outcome').allInnerTexts()).join('\n'),
      (text) => text.includes('连接请求：未通过'), 60000)
    record('无效密钥：连接验证明确失败', /AUTH|Auth|鉴权|401/.test(failed), failed.slice(0, 200))
  }

  // ── F 不可达地址：验证显示连接失败 ──
  {
    await page.getByLabel('main 请求地址', { exact: true }).fill('https://127.0.0.1:9/v1')
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await page.getByText('配置已保存', { exact: false }).first().waitFor()
    await page.getByRole('button', { name: '验证连接', exact: true }).click()
    const failed = await waitFor(async () => (await page.locator('.model-verify-outcome').allInnerTexts()).join('\n'),
      (text) => text.includes('连接请求：未通过'), 90000)
    // 换成未知端点会失去经核查的计数依据（fail-closed）或连接失败——两者都是明确失败
    record('不可达地址：连接验证明确失败',
      /ConnectError|timed out|Timeout|无法连接|MODEL_WINDOW_UNKNOWN/.test(failed), failed.slice(0, 240))
    await page.getByLabel('main 请求地址', { exact: true }).fill('https://opencode.ai/zen/go/v1')
    await page.getByLabel('main API 密钥', { exact: true }).fill(realKey)
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await page.getByText('配置已保存', { exact: false }).first().waitFor()
  }

  // ── G 保存失败：草稿保留与真实原因 ──
  {
    await page.getByLabel('main 请求地址', { exact: true }).fill('')
    await page.getByLabel('main 模型', { exact: true }).fill('custom-probe-model')
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await page.getByText('配置非法', { exact: false }).waitFor()
    const modelValue = await page.getByLabel('main 模型', { exact: true }).inputValue()
    record('保存失败保留草稿并显示原因', modelValue === 'custom-probe-model')
    await page.getByRole('button', { name: '用于主模型', exact: true }).first().click()
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await page.getByText('配置已保存', { exact: false }).first().waitFor()
  }

  // ── H 成员只读（李蕾 = member；王明是 admin 可编辑） ──
  {
    await changeIdentity(page, 'lilei')
    await openSettings(page, '模型连接')
    await page.getByText('当前身份为成员，模型配置只读。').waitFor()
    const disabled = await page.getByLabel('main API 密钥', { exact: true }).isDisabled()
    const saveDisabled = await page.getByRole('button', { name: '保存配置', exact: true }).isDisabled()
    record('成员只读：输入与保存禁用，视图可读', disabled && saveDisabled)
    await changeIdentity(page, 'owner')
  }

  // ── I 清除并重新配置密钥 ──
  {
    await openSettings(page, '模型连接')
    const auxSection = page.locator('section.model-slot').filter({ hasText: '辅助模型' })
    await auxSection.getByRole('button', { name: '清除该槽密钥', exact: true }).click()
    await page.getByRole('button', { name: '确认清除', exact: true }).click()
    await auxSection.getByText('密钥未配置', { exact: false }).waitFor()
    record('清除密钥生效（显式操作）', true)
    await page.getByLabel('aux API 密钥', { exact: true }).fill(realKey)
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await page.getByText('配置已保存', { exact: false }).first().waitFor()
    record('重新配置密钥生效', true)
  }

  // ── J 重启后持久化：不再引导 ──
  {
    await app.evaluate(() => globalThis.agentcrewLifecycle.requestExit('menu'))
    const exiting = exitOf(app.process())
    await page.getByRole('button', { name: '确认退出', exact: true }).click()
    assert.equal(await exiting, 0, '应用应正常退出')
    app = await launchApp(sessionDir)
    const repage = await app.firstWindow()
    repage.setDefaultTimeout(45000)
    await repage.getByText('任务服务已连接', { exact: true }).waitFor()
    await waitFor(() => repage.locator('.model-setup-hint').count(), (count) => count === 0)
    record('重启后配置持久：已有配置不重复引导', true)
  }

  // ── K 缺计数资源：验证在请求前明确失败 ──
  {
    const bareDir = resolve(directory, 'no-tokenizer')
    await mkdir(`${bareDir}/data`, { recursive: true })
    await cp(`${sessionDir}/data/config.json`, `${bareDir}/data/config.json`)
    const app2 = await launchApp(bareDir, { AGENTCREW_TOKENIZER_DIR: `${directory}/no-such-tokenizers` })
    const page2 = await app2.firstWindow()
    page2.setDefaultTimeout(45000)
    await page2.getByText('任务服务已连接', { exact: true }).waitFor()
    await openSettings(page2, '模型连接')
    await page2.getByRole('button', { name: '验证连接', exact: true }).click()
    const outcome = await waitFor(async () => (await page2.locator('.model-verify-outcome').allInnerTexts()).join('\n'),
      (text) => text.includes('计数资产：未通过'), 30000)
    record('缺计数资源：验证明确失败（不估算、不静默）', outcome.includes('TOKENIZER'), outcome.slice(0, 240))
    process.kill(app2.process().pid, 'SIGKILL')
  }

  // ── L 环境变量覆盖：说明实际生效来源 ──
  {
    const envDir = resolve(directory, 'env-override')
    await mkdir(`${envDir}/data`, { recursive: true })
    await cp(`${sessionDir}/data/config.json`, `${envDir}/data/config.json`)
    const app3 = await launchApp(envDir, { AGENTCREW_MAIN__MODEL: 'env-override-probe-model' })
    const page3 = await app3.firstWindow()
    page3.setDefaultTimeout(45000)
    await page3.getByText('任务服务已连接', { exact: true }).waitFor()
    await openSettings(page3, '模型连接')
    await page3.getByText('该槽有字段被环境变量覆盖', { exact: false }).first().waitFor()
    record('env 覆盖字段提示实际生效来源', true)
    process.kill(app3.process().pid, 'SIGKILL')
  }
} catch (error) {
  let context = ''
  try {
    const failurePage = app ? await app.firstWindow().catch(() => null) : null
    if (failurePage) {
      await failurePage.screenshot({ path: resolve(directory, 'failure.png') })
      const snapshot = await failurePage.evaluate(() => ({
        url: location.href,
        settingsShell: Boolean(document.querySelector('.settings-shell')),
        heading: document.querySelector('h1')?.textContent ?? '',
        bodyStart: document.body.innerText.slice(0, 300),
      }))
      context = JSON.stringify(snapshot)
    }
  } catch { /* 诊断失败不影响原始错误 */ }
  record('检查执行完整性', false, `${error?.stack ?? error}`.slice(0, 500) + ' || ' + context.slice(0, 400)
    + ' || console=' + consoleLog.slice(-25).join(' / ').slice(0, 900))
} finally {
  await mkdir(directory, { recursive: true })
  await writeFile(resolve(directory, 'm4-onboarding-report.json'),
    JSON.stringify({ generated_at: new Date().toISOString(), results }, null, 2))
  try { if (app?.process()) process.kill(app.process().pid, 'SIGKILL') } catch { /* 已退出 */ }
}

const failed = results.filter((item) => item.result === 'failed')
console.log(`\n报告：${resolve(directory, 'm4-onboarding-report.json')}`)
if (failed.length) { console.error(`失败 ${failed.length} 项`); process.exit(1) }
console.log('全部通过')
