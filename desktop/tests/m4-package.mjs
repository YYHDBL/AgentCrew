import assert from 'node:assert/strict'
import { execFileSync, spawn } from 'node:child_process'
import { cp, mkdir, readFile, stat, writeFile } from 'node:fs/promises'
import { readdir } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { _electron as electron } from 'playwright-core'

// M4-05：正式应用包核查。对 electron-builder 产物 AgentCrew.app 做布局/
// ASAR/Mach-O/图标原生解码/许可/可执行权限/禁入内容与真实凭据扫描，并从
// 生成的 .app 真实启动、经界面完成一项真实任务；DMG 存在时挂载核验。

const APP = resolve('release/mac-arm64/AgentCrew.app')
const DIRECTORY = resolve('.artifacts', `m4-package-${Date.now()}`)
const results = []
const record = (name, passed, detail = '') => {
  results.push({ check: name, result: passed ? 'passed' : 'failed', detail: String(detail).slice(0, 500) })
  console.log(`${passed ? '✓' : '✗'} ${name}${passed ? '' : '：' + detail}`)
}
const run = (command, args) => {
  try { return { code: 0, output: execFileSync(command, args, { encoding: 'utf8' }) } }
  catch (error) { return { code: error.status ?? 1, output: (error.stdout ?? '') + (error.stderr ?? '') } }
}
const sha256 = async (path) => createHash('sha256').update(await readFile(path)).digest('hex')
const waitFor = async (read, accept, timeout = 30000) => {
  const deadline = Date.now() + timeout
  let value
  while (Date.now() < deadline) {
    value = await read()
    if (accept(value)) return value
    await delay(150)
  }
  throw new Error(`状态未在期限内满足：${JSON.stringify(value)?.slice(0, 200)}`)
}

try {
  await mkdir(DIRECTORY, { recursive: true })
  assert.ok(await stat(APP).then(() => true, () => false), `缺少应用包：${APP}`)

  // ── A Info.plist 与资源布局 ──
  const plist = (name) => run('/usr/libexec/PlistBuddy', ['-c', `Print :${name}`, `${APP}/Contents/Info.plist`]).output.trim()
  record('CFBundleIdentifier = app.agentcrew.desktop', plist('CFBundleIdentifier') === 'app.agentcrew.desktop', plist('CFBundleIdentifier'))
  record('CFBundleName / CFBundleDisplayName = AgentCrew', plist('CFBundleName') === 'AgentCrew' && plist('CFBundleDisplayName') === 'AgentCrew')
  record('CFBundleShortVersionString = 0.4.0', plist('CFBundleShortVersionString') === '0.4.0', plist('CFBundleShortVersionString'))
  record('LSMinimumSystemVersion 记录为 13.0.0', plist('LSMinimumSystemVersion') === '13.0.0', plist('LSMinimumSystemVersion'))
  const resources = `${APP}/Contents/Resources`
  record('包内 sidecar 与计数资源存在', await stat(`${resources}/sidecar/darwin-arm64/agentcrew-sidecar`).then(() => true, () => false)
    && await stat(`${resources}/tokenizers/deepseek-v4.1/tokenizer.json`).then(() => true, () => false)
    && await stat(`${resources}/tokenizers/manifest.json`).then(() => true, () => false))
  const licenses = await readdir(`${resources}/licenses`).catch(() => [])
  record('第三方依赖许可随包（licenses/）', licenses.length >= 5, `count=${licenses.length}`)

  // ── B Mach-O 架构与可执行权限 ──
  const electronArches = run('lipo', ['-archs', `${APP}/Contents/MacOS/AgentCrew`]).output.trim()
  const sidecarArches = run('lipo', ['-archs', `${resources}/sidecar/darwin-arm64/agentcrew-sidecar`]).output.trim()
  record('壳与 sidecar 均为 arm64 单架构', electronArches === 'arm64' && sidecarArches === 'arm64', `electron=${electronArches} sidecar=${sidecarArches}`)
  const sidecarMode = (await stat(`${resources}/sidecar/darwin-arm64/agentcrew-sidecar`)).mode
  record('sidecar 保留可执行权限', (sidecarMode & 0o111) !== 0, `mode=${(sidecarMode & 0o777).toString(8)}`)

  // ── C ASAR 清单与禁入内容 ──
  const asarList = run('npx', ['asar', 'list', `${resources}/app.asar`]).output
  record('ASAR 含主进程/preload/renderer', asarList.includes('/out/main/index.js') && asarList.includes('/out/preload/index.js') && asarList.includes('/out/renderer/index.html'))
  const forbidden = asarList.split('\n').filter((line) => /\/tests\/|\/config\.json$|\/\.env$|agentcrew\.db$|sidecar-launch\.log$|package-lock\.json$/.test(line))
  record('ASAR 无开发数据库/配置密钥/日志/测试材料', forbidden.length === 0, forbidden.slice(0, 5).join(','))
  const resourcesTree = await readdir(resources, { recursive: true, withFileTypes: false }).catch(() => [])
  const forbiddenResources = resourcesTree.filter((item) => /^(config\.json|\.env|agentcrew\.db)$/.test(String(item).split('/').pop() ?? ''))
  record('Resources 无开发数据文件', forbiddenResources.length === 0, String(forbiddenResources.slice(0, 3)))

  // ── D 真实凭据扫描（只扫真实密钥值；AGENTCREW_TOKEN 是变量名不是机密值） ──
  const realConfig = JSON.parse(await readFile('../backend/data/config.json', 'utf8'))
  const secrets = Object.values(realConfig.models).map((slot) => slot.api_key).filter((key) => key && key.length >= 16)
  let packageText = ''
  for (const relative of ['Contents/Resources/app.asar', 'Contents/Info.plist']) {
    packageText += (await readFile(`${APP}/${relative}`)).toString('latin1')
  }
  const leaked = secrets.filter((secret) => secret && packageText.includes(secret))
  record('真实模型密钥不出现在应用包', leaked.length === 0, leaked.length ? 'leaked' : '')

  // ── E 图标原生解码 ──
  const icnsInfo = run('sips', ['-g', 'pixelWidth', '-g', 'pixelHeight', `${resources}/icon.icns`])
  record('ICNS 原生解码', icnsInfo.code === 0 && /pixelWidth: \d+/.test(icnsInfo.output), icnsInfo.output.split('\n')[1] ?? '')
  const pngInfo = run('sips', ['-g', 'pixelWidth', 'assets/brand/agentcrew-icon.png'])
  record('品牌 PNG 原生解码（1024）', /pixelWidth: 1024/.test(pngInfo.output), pngInfo.output.split('\n')[1] ?? '')

  // ── F 从生成的 .app 真实启动并经界面完成真实任务 ──
  {
    const sessionDir = resolve(DIRECTORY, 'run')
    await mkdir(`${sessionDir}/data`, { recursive: true })
    await cp('../backend/data/config.json', `${sessionDir}/data/config.json`)
    const app = await electron.launch({
      args: [`--user-data-dir=${sessionDir}`],
      executablePath: `${APP}/Contents/MacOS/AgentCrew`
    })
    const page = await app.firstWindow()
    page.setDefaultTimeout(60000)
    await page.getByText('任务服务已连接', { exact: true }).waitFor()
    const packaged = await app.evaluate(() => process.resourcesPath)
    record('以打包模式启动（resourcesPath 指向 .app 内）', String(packaged).includes('AgentCrew.app/Contents/Resources'), String(packaged))
    const target = `${sessionDir}/data/workspaces/default/m4-package.txt`
    await page.getByRole('textbox', { name: '任务指令', exact: true }).fill(
      `请调用 write_file 把一行文字 “package ${Date.now()}” 写入文件 ${target}，写完并读取确认后回复“已完成”。不要做其他事。`)
    await page.getByRole('button', { name: '发送任务', exact: true }).click()
    await page.getByRole('button', { name: '仅本次允许', exact: true }).first().waitFor({ timeout: 180000 })
    await page.getByRole('button', { name: '仅本次允许', exact: true }).first().click()
    const conversation = await waitFor(() => page.evaluate(() => sessionStorage.getItem('conversation')), Boolean)
    const request = (path) => page.evaluate(async (path) => {
      const port = await window.agentcrew.getBackendPort()
      const token = await window.agentcrew.getToken()
      const response = await fetch(`http://127.0.0.1:${port}/api${path}`, { headers: { Authorization: `Bearer ${token}` } })
      return (await response.json()).data
    }, path)
    const runs = await waitFor(() => request(`/conversations/${conversation}/task-runs`), (items) => items.at(-1)?.status === 'completed', 300000)
    record('打包应用经界面完成真实任务（审批-写入）', runs.at(-1).status === 'completed'
      && (await readFile(target, 'utf8')).includes('package'))
    await app.evaluate(() => globalThis.agentcrewLifecycle.requestExit('menu'))
    const exiting = new Promise((done) => app.process().once('exit', (code) => done(code)))
    await page.getByRole('button', { name: '确认退出', exact: true }).click()
    record('打包应用正常退出', await exiting === 0)
  }

  // ── G DMG（存在时挂载核验） ──
  const dmg = resolve('release/AgentCrew-0.4.0-arm64.dmg')
  if (await stat(dmg).then(() => true, () => false)) {
    const attached = run('hdiutil', ['attach', '-nobrowse', '-plist', dmg])
    const mountPoint = /<string>(\/Volumes\/[^<]+)<\/string>/.exec(attached.output)?.[1]
    assert.ok(mountPoint, `DMG 挂载失败：${attached.output.slice(0, 200)}`)
    try {
      const entries = await readdir(mountPoint)
      const hasApp = entries.includes('AgentCrew.app')
      const hasApplications = entries.includes('Applications')
      record('DMG 含 AgentCrew.app 与 Applications 拖入入口', hasApp && hasApplications, entries.join(','))
      record('DMG 中的应用完整可执行', await stat(`${mountPoint}/AgentCrew.app/Contents/MacOS/AgentCrew`).then(() => true, () => false))
    } finally { run('hdiutil', ['detach', mountPoint, '-force']) }
    // .app 是目录：可交付产物以 DMG 为准，另记壳二进制/ASAR/sidecar 关键哈希
    const manifest = {
      dmg: { path: 'release/AgentCrew-0.4.0-arm64.dmg', sha256: await sha256(dmg), bytes: (await stat(dmg)).size },
      macos_binary_sha256: await sha256(`${APP}/Contents/MacOS/AgentCrew`),
      app_asar_sha256: await sha256(`${APP}/Contents/Resources/app.asar`),
      sidecar_sha256: await sha256(`${APP}/Contents/Resources/sidecar/darwin-arm64/agentcrew-sidecar`),
      tokenizer_sha256: await sha256(`${APP}/Contents/Resources/tokenizers/deepseek-v4.1/tokenizer.json`),
    }
    await writeFile(resolve(DIRECTORY, 'package-hashes.json'), JSON.stringify(manifest, null, 2) + '\n')
    record('应用与 DMG SHA-256 清单已生成', true, JSON.stringify(manifest).slice(0, 200))
  } else {
    record('DMG 挂载核验（本次未产出 DMG，dist:mac 后复跑覆盖）', false, `not found: ${dmg}`)
  }
} catch (error) {
  record('检查执行完整性', false, `${error?.stack ?? error}`.slice(0, 600))
} finally {
  await writeFile(resolve(DIRECTORY, 'm4-package-report.json'), JSON.stringify({ generated_at: new Date().toISOString(), results }, null, 2))
}

const failed = results.filter((item) => item.result === 'failed')
console.log(`\n报告：${resolve(DIRECTORY, 'm4-package-report.json')}`)
if (failed.length) { console.error(`失败 ${failed.length} 项`); process.exit(1) }
console.log('全部通过')
