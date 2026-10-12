import assert from 'node:assert/strict'
import { execFileSync, spawn } from 'node:child_process'
import { cp, mkdir, readFile, stat, writeFile, chmod } from 'node:fs/promises'
import { createServer } from 'node:net'
import { resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { _electron as electron } from 'playwright-core'

// M4-03：生产启动、监管与数据路径。全部使用真实打包 sidecar
//（desktop/resources/sidecar/darwin-arm64/agentcrew-sidecar，M4-02 产物），
// 通过 AGENTCREW_SIDECAR_BINARY（仅未打包态生效）驱动真实 Electron 主进程
// 的生产启动路径。Electron 会话串行运行；异常清理只终止本检查拥有的进程。

const BINARY = resolve('resources/sidecar/darwin-arm64/agentcrew-sidecar')
const EXECUTABLE = resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron')
const directory = resolve('.artifacts', `m4-sidecar-${Date.now()}`)
const evidence = []
const results = []

const record = (name, passed, detail = '') => {
  results.push({ check: name, result: passed ? 'passed' : 'failed', detail: String(detail).slice(0, 800) })
  console.log(`${passed ? '✓' : '✗'} ${name}${passed ? '' : '：' + detail}`)
}

const psLines = () => execFileSync('ps', ['-axo', 'pid,pgid,command'], { encoding: 'utf8' }).split('\n')
// onefile sidecar 是父子两个进程（bootloader + 解包子进程），同一实例共享
// 进程组：按 pgid 去重后计数才是"任务服务实例数"
const ownGroups = (match) => {
  const groups = new Set()
  for (const line of psLines()) {
    if (!line.includes('agentcrew-sidecar') || !line.includes(match)) continue
    const [, pgid] = line.trim().split(/\s+/)
    groups.add(Number(pgid))
  }
  return [...groups]
}
const killPgid = (pgids) => {
  for (const pgid of pgids) {
    try { process.kill(-pgid, 'SIGKILL') } catch { /* 已退出 */ }
  }
}
const sidecarEnv = (token, extra = {}) => ({
  PATH: '/usr/bin:/bin', HOME: process.env.HOME, LANG: 'en_US.UTF-8',
  AGENTCREW_TOKEN: token, ...extra
})
const waitFor = async (read, accept, timeout = 45000) => {
  const deadline = Date.now() + timeout
  let value
  while (Date.now() < deadline) {
    value = await read()
    if (accept(value)) return value
    await delay(150)
  }
  throw new Error(`真实状态未在期限内满足核验条件：${JSON.stringify(value)?.slice(0, 400)}`)
}
const exitOf = (childProcess, timeout = 30000) => new Promise((done) => {
  const timer = setTimeout(() => done('timeout'), timeout)
  childProcess.once('exit', (code) => { clearTimeout(timer); done(code) })
})

const launchApp = async (sessionDir, env = {}) => electron.launch({
  args: ['.', `--user-data-dir=${sessionDir}`], executablePath: EXECUTABLE,
  env: { ...process.env, AGENTCREW_SIDECAR_BINARY: BINARY, ...env }
})
const prepareSession = async (name) => {
  const sessionDir = resolve(directory, name)
  await mkdir(`${sessionDir}/data`, { recursive: true })
  await cp('../backend/data/config.json', `${sessionDir}/data/config.json`)
  return sessionDir
}
const sidecarPidOf = (sessionDir) => {
  const groups = ownGroups(sessionDir)
  return { count: groups.length, pid: groups[0] ?? null }
}

try {
  await mkdir(directory, { recursive: true })
  await stat(BINARY)

  // ── S0 端口占用：真实二进制 --port 被占时按契约改用 +1 ──
  {
    const blockerPort = await new Promise((done) => {
      const server = createServer()
      server.listen(0, '127.0.0.1', () => { const { port } = server.address(); done(port) })
    })
    const dataDir = resolve(directory, 'data-port')
    await mkdir(dataDir, { recursive: true })
    const child = spawn(BINARY, ['--port', String(blockerPort), '--data-dir', dataDir, '--parent-pid', String(process.pid)],
      { env: sidecarEnv('m4-port-test-token'), cwd: directory, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
    let stdout = ''
    child.stdout.on('data', (chunk) => { stdout += chunk.toString() })
    const deadline = Date.now() + 30000
    let reportedPort = null
    while (Date.now() < deadline && reportedPort === null) {
      const match = /AGENTCREW_READY \{"port":\s*(\d+)\}/.exec(stdout)
      if (match) reportedPort = Number(match[1])
      else await delay(100)
    }
    // 契约：请求端口被占时在 +1..+3 内换端口（sidecar.ts 就绪校验同一范围）
    record('端口占用：请求端口被占时在 +1..+3 内换端口',
      reportedPort !== null && reportedPort > blockerPort && reportedPort <= blockerPort + 3,
      `requested=${blockerPort} ready=${reportedPort} stdout=${stdout.slice(0, 200)}`)
    process.kill(-child.pid, 'SIGTERM')
    const exitCode = await exitOf(child)
    record('端口占用场景 SIGTERM 优雅退出', exitCode === 0, `exit=${exitCode}`)
  }

  // ── S1 主会话：就绪、单实例、SIGKILL 自动重启、旧 token 失效、
  //     并发 restart 去重、二次启动聚焦、正常退出无残留 ──
  {
    const sessionDir = await prepareSession('main')
    const app = await launchApp(sessionDir)
    const page = await app.firstWindow()
    page.setDefaultTimeout(45000)
    await page.getByText('任务服务已连接', { exact: true }).waitFor()
    const first = await page.evaluate(async () => ({ port: await window.agentcrew.getBackendPort(), token: await window.agentcrew.getToken() }))
    let snapshot = sidecarPidOf(sessionDir)
    record('首次就绪：一个有效任务服务进程', snapshot.count === 1 && first.port !== null && first.token !== null,
      `processes=${snapshot.count} port=${first.port} pid=${snapshot.pid}`)

    process.kill(-snapshot.pid, 'SIGKILL')
    const second = await waitFor(() => page.evaluate(async () => ({ port: await window.agentcrew.getBackendPort(), token: await window.agentcrew.getToken() })),
      (value) => value.port !== null && value.port !== first.port)
    await page.getByText('任务服务已连接', { exact: true }).waitFor()
    snapshot = sidecarPidOf(sessionDir)
    record('SIGKILL 后自动重启（新进程、新端口）', snapshot.count === 1 && second.port !== first.port,
      `new_pid=${snapshot.pid} old_port=${first.port} new_port=${second.port}`)
    // /api/health 供壳的健康轮询使用、不要求鉴权；令牌语义在数据接口上核查
    const tokenCheck = await page.evaluate(async ({ port, oldToken, newToken }) => {
      const request = (token) => fetch(`http://127.0.0.1:${port}/api/settings`, { headers: { Authorization: `Bearer ${token}` } }).then((response) => response.status)
      return { old: await request(oldToken), fresh: await request(newToken) }
    }, { port: second.port, oldToken: first.token, newToken: second.token })
    record('旧令牌对新实例不可用、新令牌可用（本地认证保持）',
      tokenCheck.old === 401 && tokenCheck.fresh === 200,
      `old=${tokenCheck.old} fresh=${tokenCheck.fresh}`)

    await page.evaluate(async () => { await Promise.all([window.agentcrew.restartBackend(), window.agentcrew.restartBackend()]) })
    await page.getByText('任务服务已连接', { exact: true }).waitFor()
    snapshot = sidecarPidOf(sessionDir)
    record('并发重启请求去重：完成后仍单实例', snapshot.count === 1, `processes=${snapshot.count}`)
    const after = await page.evaluate(async () => ({ port: await window.agentcrew.getBackendPort(), token: await window.agentcrew.getToken() }))
    record('重启轮换端口与令牌', after.port !== second.port && after.token !== second.token, `port ${second.port}→${after.port}`)

    const secondInstance = spawn(EXECUTABLE, ['.', `--user-data-dir=${sessionDir}`],
      { env: { ...process.env, AGENTCREW_SIDECAR_BINARY: BINARY }, stdio: 'ignore' })
    const secondExit = await exitOf(secondInstance, 20000)
    record('二次启动被单实例锁拒绝并聚焦既有窗口', secondExit === 0,
      `exit=${secondExit}`)
    await delay(500)
    const visible = await page.evaluate(() => document.visibilityState)
    record('既有窗口在二次启动后保持可见', visible === 'visible', `visibilityState=${visible}`)
    snapshot = sidecarPidOf(sessionDir)
    record('二次启动未产生第二个任务服务', snapshot.count === 1, `processes=${snapshot.count}`)

    await app.evaluate(() => globalThis.agentcrewLifecycle.requestExit('menu'))
    const exiting = exitOf(app.process())
    await page.getByRole('button', { name: '确认退出', exact: true }).click()
    const exitCode = await exiting
    await delay(1500)
    record('正常退出（确认后应用退出）', exitCode === 0, `exit=${exitCode}`)
    record('正常退出后无 sidecar 残留', ownGroups(sessionDir).length === 0,
      `leftover=${ownGroups(sessionDir).length}`)
  }

  // ── S2 父进程终止：主进程死亡 → sidecar 自行退出 ──
  {
    const sessionDir = await prepareSession('parent-kill')
    const app = await launchApp(sessionDir)
    const page = await app.firstWindow()
    page.setDefaultTimeout(45000)
    await page.getByText('任务服务已连接', { exact: true }).waitFor()
    const snapshot = sidecarPidOf(sessionDir)
    assert.equal(snapshot.count, 1, '父进程终止场景应只有一个任务服务进程')
    process.kill(app.process().pid, 'SIGKILL')
    const leftover = await waitFor(() => ownGroups(sessionDir).length, (count) => count === 0, 20000).catch(() => ownGroups(sessionDir).length)
    record('父进程终止后 sidecar 自行退出（无孤儿）', leftover === 0, `leftover=${leftover}`)
  }

  // ── S3 启动预算：真实慢启动进程超过 30 秒预算被判定失败并安排重启 ──
  {
    const sessionDir = await prepareSession('budget')
    const wrapper = resolve(directory, 'slow-sidecar.sh')
    await writeFile(wrapper, `#!/bin/bash\nsleep 45\nexec "${BINARY}" "$@"\n`)
    await chmod(wrapper, 0o755)
    const app = await launchApp(sessionDir, { AGENTCREW_SIDECAR_BINARY: wrapper })
    const launchLog = resolve(sessionDir, 'sidecar-launch.log')
    const timedOut = await waitFor(async () => (await readFile(launchLog, 'utf8').catch(() => '')).includes('启动超过 30 秒'), Boolean, 50000).catch(() => false)
    record('超过 30 秒启动预算被真实计时判定失败', timedOut, `launch_log=${launchLog}`)
    process.kill(app.process().pid, 'SIGKILL')
    killPgid(psLines().filter((line) => line.includes('slow-sidecar.sh')).map((line) => Number(line.trim().split(/\s+/)[1])))
    await delay(500)
    record('预算场景清理后无本检查拥有的残留', ownGroups(sessionDir).length === 0 && psLines().filter((line) => line.includes('slow-sidecar.sh')).length === 0)
  }

  // ── S4 资源不可用：启动进程失败进入重启上限（不回退开发命令）──
  {
    const sessionDir = await prepareSession('missing')
    const missingBinary = resolve(directory, 'nonexistent-sidecar')
    const app = await launchApp(sessionDir, { AGENTCREW_SIDECAR_BINARY: missingBinary })
    const launchLog = resolve(sessionDir, 'sidecar-launch.log')
    const limited = await waitFor(async () => (await readFile(launchLog, 'utf8').catch(() => '')).includes('已达到自动重启上限'), Boolean, 60000).catch(() => false)
    const log = await readFile(launchLog, 'utf8').catch(() => '')
    record('启动进程失败按上限停止自动重启（明确错误与日志位置）', limited && log.includes('启动进程失败'), log.slice(0, 300))
    process.kill(app.process().pid, 'SIGKILL')
    await delay(500)
    record('失败场景清理后无残留', ownGroups(sessionDir).length === 0)
  }
} catch (error) {
  record('检查执行完整性', false, `${error?.stack ?? error}`)
} finally {
  evidence.push({ generated_at: new Date().toISOString(), results })
  await writeFile(resolve(directory, 'm4-sidecar-report.json'), JSON.stringify(evidence[0], null, 2))
  // 兜底清理：仅本检查目录拥有的 sidecar 进程
  killPgid(ownGroups(directory))
}

const failed = results.filter((item) => item.result === 'failed')
console.log(`\n报告：${resolve(directory, 'm4-sidecar-report.json')}`)
if (failed.length) { console.error(`失败 ${failed.length} 项`); process.exit(1) }
console.log('全部通过')
