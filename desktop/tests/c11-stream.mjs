import assert from 'node:assert/strict'
import { _electron as electron } from 'playwright-core'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { createServer, request as httpRequest } from 'node:http'
import { once } from 'node:events'
import { getLines, getMessages } from '@microsoft/fetch-event-source/lib/cjs/parse.js'

// 使用真实 Electron、后端与 GLM，检查断线恢复和恢复后的增量。
const directory = resolve('.artifacts', `c11-stream-${Date.now()}`)
await mkdir(`${directory}/data`, { recursive: true })
const config = JSON.parse(await readFile('../backend/data/config.json', 'utf8'))
const modelEndpoint = config.models.main.base_url
assert.ok(modelEndpoint)
let interruptModel = false
let modelInterruptions = 0
let heldStream = null
let holdFrom = null
const controls = []
// 对真实会话流施加读取背压，控制帧仍由后端自行产生。
const backendProxy = createServer((incoming, outgoing) => {
  const target = new URL(incoming.url)
  assert.equal(target.hostname, '127.0.0.1')
  const upstream = httpRequest(target, { method: incoming.method, headers: incoming.headers }, response => {
    outgoing.writeHead(response.statusCode, response.headers)
    outgoing.flushHeaders()
    const parse = getLines(getMessages(() => {}, () => {}, message => {
      if (message.event === 'resync' || message.event === 'shutdown') controls.push({ type: message.event, data: JSON.parse(message.data), time: Date.now() })
    }))
    const forward = () => {
      if (target.pathname.endsWith('/stream')) response.on('data', parse)
      response.pipe(outgoing)
    }
    if (holdFrom !== null && target.pathname.endsWith('/stream') && target.searchParams.get('from') === String(holdFrom)) {
      heldStream = { response, forward }
      holdFrom = null
    } else forward()
    response.on('error', error => outgoing.destroy(error))
  })
  upstream.on('error', error => outgoing.destroy(error))
  outgoing.on('close', () => upstream.destroy())
  incoming.pipe(upstream)
})
backendProxy.listen(0, '127.0.0.1')
await once(backendProxy, 'listening')
// 转发真实模型响应，在已经传输文本后中断一次连接。
const modelProxy = createServer(async (incoming, outgoing) => {
  const headers = { ...incoming.headers, 'accept-encoding': 'identity' }
  delete headers.host
  delete headers.connection
  delete headers['transfer-encoding']
  const upstream = await fetch(modelEndpoint + incoming.url, { method: incoming.method, headers, body: incoming, duplex: 'half' })
  outgoing.writeHead(upstream.status, Object.fromEntries([...upstream.headers].filter(([name]) => !['content-encoding', 'content-length', 'transfer-encoding', 'connection'].includes(name))))
  let interrupted = false
  const parse = getLines(getMessages(() => {}, () => {}, message => {
    if (!interruptModel || !message.data) return
    const data = JSON.parse(message.data)
    if (data.delta?.type === 'text_delta' && data.delta.text) { interrupted = true; interruptModel = false }
  }))
  for await (const chunk of upstream.body) {
    outgoing.write(chunk)
    parse(chunk)
    if (interrupted) {
      await new Promise(resolve => setTimeout(resolve, 100))
      modelInterruptions++
      outgoing.destroy()
      break
    }
  }
  if (!interrupted) outgoing.end()
})
modelProxy.listen(0, '127.0.0.1')
await once(modelProxy, 'listening')
config.models.main.base_url = `http://127.0.0.1:${modelProxy.address().port}`
await writeFile(`${directory}/data/config.json`, JSON.stringify(config))
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`], executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const page = await app.firstWindow()
page.setDefaultTimeout(180000)
const requests = []
const errors = []
const output = []
page.on('request', request => {
  const url = new URL(request.url())
  if (url.pathname.startsWith('/api/')) requests.push({ path: url.pathname + url.search, time: Date.now() })
})
page.on('pageerror', error => errors.push(error.message))
const record = value => { output.push(value); console.log(JSON.stringify(value)) }
const request = (path, body) => page.evaluate(async ({ path, body }) => {
  const [port, token] = await Promise.all([window.agentcrew.getBackendPort(), window.agentcrew.getToken()])
  const response = await fetch(`http://127.0.0.1:${port}/api${path}`, {
    method: body === undefined ? 'GET' : 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return (await response.json()).data
}, { path, body })
const send = async text => {
  await page.getByRole('textbox', { name: '任务指令', exact: true }).fill(text)
  await page.getByRole('button', { name: '发送任务', exact: true }).click()
}
const backendPid = () => {
  const line = execFileSync('ps', ['-axo', 'pid,command'], { encoding: 'utf8' }).split('\n').find(line => line.includes('/python3 -m agentcrew_server') && line.includes(directory))
  assert.ok(line)
  return Number(line.trim().split(/\s+/)[0])
}
const waitForRestart = oldPort => page.evaluate(async oldPort => {
  const deadline = Date.now() + 30000
  while (Date.now() < deadline) {
    const port = await window.agentcrew.getBackendPort()
    if (port && port !== oldPort) return port
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  throw new Error('后端未在期限内重新启动')
}, oldPort)
try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  await send('必须使用 ask_user 提问“前端恢复检查”，等待回答后原样回复答案。')
  await page.getByRole('textbox', { name: '问题回答', exact: true }).waitFor()
  const id = await page.evaluate(() => sessionStorage.getItem('conversation'))
  await page.reload()
  await page.getByRole('textbox', { name: '问题回答', exact: true }).waitFor()
  assert.equal(await page.locator('.message.user').count(), 1)
  await page.getByRole('textbox', { name: '问题回答', exact: true }).fill('前端恢复检查完成')
  await page.getByRole('button', { name: '提交回答', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.notice')?.textContent?.includes('· idle'))
  const initial = await request(`/conversations/${id}/state`)
  record({ check: 'question-refresh-answer', passed: true, head: initial.at_global_seq })

  const oldPort = await page.evaluate(() => window.agentcrew.getBackendPort())
  const offset = requests.length
  process.kill(backendPid(), 'SIGKILL')
  await waitForRestart(oldPort)
  await page.waitForFunction(() => document.querySelector('.notice')?.textContent?.startsWith('已连接'))
  const reconnect = requests.slice(offset).filter(({ path }) => path.includes(`/conversations/${id}/`))
  const snapshotIndex = reconnect.findIndex(({ path }) => path.endsWith('/state'))
  const streamIndex = reconnect.findIndex(({ path }) => path.includes('/stream?'))
  record({ check: 'reconnect-snapshot-order', requests: reconnect, snapshotIndex, streamIndex })
  assert.ok(snapshotIndex >= 0 && snapshotIndex < streamIndex, '重连必须先获取 state，再读取事件')
  assert.equal(await page.locator('.message.user').count(), 1)
  await send('仅回复“恢复后的新增指令完成”，不要调用工具。')
  await page.waitForFunction(() => document.querySelectorAll('.message.assistant').length === 2 && document.querySelector('.notice')?.textContent?.includes('· idle'))
  const after = await request(`/conversations/${id}/state`)
  assert.ok(after.at_global_seq > initial.at_global_seq)
  assert.equal(await page.locator('.message.user').count(), 2)
  record({ check: 'restart-incremental-events', passed: true, head: after.at_global_seq })

  await page.getByRole('button', { name: '＋ 新建任务', exact: true }).click()
  interruptModel = true
  await send('请写一篇两百字左右的中文文章，解释春季植物生长的过程。直接输出文章，不要调用工具。')
  await page.waitForFunction(() => document.querySelector('.notice')?.textContent?.includes('· idle') && document.querySelector('.message.assistant'))
  const retryId = await page.evaluate(() => sessionStorage.getItem('conversation'))
  const runs = await request(`/conversations/${retryId}/task-runs`)
  const history = await request(`/task-runs/${runs[0].id}/events?after_seq=0&limit=500`)
  assert.equal(modelInterruptions, 1)
  const failed = history.items.find(event => event.type === 'llm.request_failed')
  assert.ok(failed, '真实传输中断必须产生模型请求失败事件')
  assert.ok(history.items.some(event => event.type === 'llm.request_done' && event.payload.step_id === failed.payload.step_id))
  assert.ok(history.items.some(event => event.type === 'run.completed'))
  record({ check: 'model-retry-events', failed: failed.global_seq, completed: history.items.at(-1).global_seq })
  assert.equal(await page.getByRole('heading', { name: '模型请求失败 · 已恢复', exact: true }).count(), 1)
  assert.equal(await page.getByRole('heading', { name: '执行错误', exact: true }).count(), 0)
  await page.reload()
  await page.getByRole('heading', { name: '模型请求失败 · 已恢复', exact: true }).waitFor()
  record({ check: 'model-retry-rendering', passed: true })

  await page.getByRole('button', { name: '＋ 新建任务', exact: true }).click()
  await send('必须调用 ask_user 提问“队列背压检查等待”，等待我回答。')
  await page.getByRole('textbox', { name: '问题回答', exact: true }).waitFor()
  const pressureId = await page.evaluate(() => sessionStorage.getItem('conversation'))
  const pressureState = await request(`/conversations/${pressureId}/state`)
  holdFrom = pressureState.at_global_seq
  await app.evaluate(({ session }, address) => session.defaultSession.setProxy({ proxyRules: address, proxyBypassRules: '<-loopback>' }), `http://127.0.0.1:${backendProxy.address().port}`)
  await page.reload()
  await page.waitForFunction(() => document.querySelector('.notice')?.textContent?.startsWith('已连接'))
  assert.ok(heldStream, '实时会话流必须已经进入真实代理的背压状态')
  const credentials = await page.evaluate(async () => ({ port: await window.agentcrew.getBackendPort(), token: await window.agentcrew.getToken() }))
  const direct = async (path, body) => {
    const response = await fetch(`http://127.0.0.1:${credentials.port}/api${path}`, { method: body === undefined ? 'GET' : 'POST', headers: { Authorization: `Bearer ${credentials.token}`, 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000) })
    assert.ok(response.ok, `真实后端返回 HTTP ${response.status}`)
    const text = await response.text()
    return text ? JSON.parse(text).data : undefined
  }
  await direct(`/conversations/${pressureId}/instructions`, { text: '压力检查。'.repeat(500000), client_request_id: crypto.randomUUID() })
  for (let index = 0; index < 1005; index++) {
    await direct(`/conversations/${pressureId}/instructions`, { text: `保留排队指令 ${index}，等待用户继续。`, client_request_id: crypto.randomUUID() })
    if (index % 200 === 0) record({ check: 'real-queue-pressure', queued: index + 2 })
  }
  const pressureHead = await direct(`/conversations/${pressureId}/state`)
  assert.equal(pressureHead.queue.length, 1006)
  heldStream.forward()
  heldStream = null
  await page.waitForFunction(() => document.querySelector('.queue')?.textContent?.includes('排队中 · 1006 条') && document.querySelectorAll('.message.user').length === 1007)
  await page.waitForFunction(() => document.querySelector('.notice')?.textContent?.startsWith('已连接'))
  const resync = controls.find(control => control.type === 'resync')
  assert.ok(resync, '真实后端必须发送 resync，不能用普通断线代替')
  const recoveredState = requests.find(({ path, time }) => path === `/api/conversations/${pressureId}/state` && time >= resync.time)
  assert.ok(recoveredState)
  assert.ok(recoveredState.time - resync.time < 1000, 'resync 恢复必须立即请求快照')
  assert.ok(requests.some(({ path, time }) => path === `/api/conversations/${pressureId}/stream?from=0` && time >= recoveredState.time))
  assert.ok(requests.some(({ path, time }) => path === `/api/conversations/${pressureId}/stream?from=${pressureHead.at_global_seq}` && time >= recoveredState.time))
  record({ check: 'resync-full-recovery', passed: true, cursor: resync.data.last_continuous_global_seq, head: pressureHead.at_global_seq, snapshotDelayMs: recoveredState.time - resync.time, messages: 1007 })
  await direct(`/conversations/${pressureId}/queue/cancel`, { all: true })
  await direct(`/task-runs/${pressureHead.current_task_run_id}/cancel`, {})
  await page.waitForFunction(() => document.querySelector('.notice')?.textContent?.includes('· idle') && !document.querySelector('.queue'))
  const shutdownState = await direct(`/conversations/${pressureId}/state`)
  await page.evaluate(id => sessionStorage.removeItem(`cursor:${id}`), pressureId)
  const shutdownPort = credentials.port
  process.kill(backendPid(), 'SIGTERM')
  await page.waitForFunction(id => sessionStorage.getItem(`cursor:${id}`) !== null, pressureId)
  const savedCursor = await page.evaluate(id => Number(sessionStorage.getItem(`cursor:${id}`)), pressureId)
  const shutdown = controls.find(control => control.type === 'shutdown')
  assert.ok(shutdown, '优雅关闭必须发送真实 shutdown 帧')
  assert.equal(savedCursor, shutdown.data.last_continuous_global_seq)
  assert.equal(savedCursor, shutdownState.at_global_seq)
  await waitForRestart(shutdownPort)
  await page.waitForFunction(() => document.querySelector('.notice')?.textContent?.startsWith('已连接'))
  assert.equal(await page.locator('.message.user').count(), 1007)
  record({ check: 'shutdown-cursor-reconnect', passed: true, cursor: savedCursor })
  assert.deepEqual(errors, [])
} finally {
  await writeFile(`${directory}/output.json`, JSON.stringify(output, null, 2))
  await app.close()
  modelProxy.closeAllConnections()
  await new Promise(resolve => modelProxy.close(resolve))
  backendProxy.closeAllConnections()
  await new Promise(resolve => backendProxy.close(resolve))
}
