import assert from 'node:assert/strict'
import { _electron as electron } from 'playwright-core'
import { mkdir, copyFile, readFile, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'

// 真实中断保留旧审批事件，再通过界面恢复并批准同一任务的新审批。
const directory = resolve('.artifacts', `c11-recovery-${Date.now()}`)
await mkdir(`${directory}/data`, { recursive: true })
await copyFile('../backend/data/config.json', `${directory}/data/config.json`)
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`], executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const page = await app.firstWindow()
page.setDefaultTimeout(180000)
const output = { directory }
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
const request = (path) => page.evaluate(async (path) => {
  const port = await window.agentcrew.getBackendPort()
  const token = await window.agentcrew.getToken()
  const response = await fetch(`http://127.0.0.1:${port}/api${path}`, { headers: { Authorization: `Bearer ${token}` } })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return (await response.json()).data
}, path)
const waitFor = async (read, accepts) => {
  const deadline = Date.now() + 180000
  while (Date.now() < deadline) {
    const value = await read()
    if (accepts(value)) return value
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error('真实状态未在期限内满足核验条件')
}

try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  await page.getByRole('textbox', { name: '任务指令', exact: true }).fill('请使用 write_file 在当前工作空间创建 c11-recovery/recovered.txt，内容为 C11 recovery completed，然后使用 read_file 读取实际文件并完成任务。恢复执行时根据副作用账本完成尚未成功写入的文件；只有文件实际写入完成且读取一致后才能回复完成。')
  await page.getByRole('button', { name: '发送任务', exact: true }).click()
  await page.getByRole('button', { name: '允许', exact: true }).waitFor()
  const conversation = await page.evaluate(() => sessionStorage.getItem('conversation'))
  const [run] = await request(`/conversations/${conversation}/task-runs`)
  const before = await request(`/task-runs/${run.id}/events?after_seq=0&limit=500`)
  assert.ok(before.items.some((event) => event.type === 'permission.requested'))
  const oldPort = await page.evaluate(() => window.agentcrew.getBackendPort())
  const processLine = execFileSync('ps', ['-axo', 'pid,command'], { encoding: 'utf8' }).split('\n').find((line) => line.includes('/python3 -m agentcrew_server') && line.includes(directory))
  assert.ok(processLine)
  const pid = Number(processLine.trim().split(/\s+/)[0])
  process.kill(pid, 'SIGKILL')
  await page.getByRole('button', { name: '恢复', exact: true }).waitFor()
  await waitFor(() => page.evaluate(() => window.agentcrew.getBackendPort()), (port) => port && port !== oldPort)
  assert.equal(await page.locator('.approval').count(), 0)
  assert.equal((await request(`/conversations/${conversation}/task-runs`))[0].status, 'interrupted')
  output.interrupted_without_resume = { approvals: 0, conversation, task_run_id: run.id, killed_pid: pid, old_port: oldPort, new_port: await page.evaluate(() => window.agentcrew.getBackendPort()) }
  await page.getByRole('button', { name: '恢复', exact: true }).click()
  const history = await waitFor(() => request(`/task-runs/${run.id}/events?after_seq=0&limit=500`), (history) => {
    const resumed = history.items.find((event) => event.type === 'run.resumed')
    return resumed && history.items.some((event) => event.type === 'permission.requested' && event.global_seq > resumed.global_seq)
  })
  const resumed = history.items.find((event) => event.type === 'run.resumed')
  const permission = history.items.find((event) => event.type === 'permission.requested' && event.global_seq > resumed.global_seq)
  assert.equal(permission.task_run_id, run.id)
  await page.getByRole('heading', { name: '已恢复 · 第 2 次尝试', exact: true }).waitFor()
  const approval = page.locator('.approval').last().getByRole('button', { name: '允许', exact: true })
  await approval.waitFor()
  assert.equal(await approval.isEnabled(), true)
  await approval.click()
  await waitFor(() => request(`/task-runs/${run.id}/events?after_seq=0&limit=500`), (history) => history.items.some((event) => event.type === 'permission.resolved' && event.payload.tool_call_id === permission.payload.tool_call_id && event.payload.decision === 'allow_once'))
  await waitFor(() => request(`/conversations/${conversation}/task-runs`), (runs) => runs[0].status === 'completed')
  const scope = await request(`/conversations/${conversation}/scope`)
  assert.equal((await readFile(`${scope.workspace_dir}/c11-recovery/recovered.txt`, 'utf8')).trim(), 'C11 recovery completed')
  await page.reload()
  await page.getByRole('heading', { name: '已恢复 · 第 2 次尝试', exact: true }).waitFor()
  assert.equal(await page.locator('.approval').count(), 0)
  assert.equal(await page.getByRole('button', { name: '恢复', exact: true }).count(), 0)
  assert.deepEqual(errors, [])
  output.resumed_approval = { task_run_id: run.id, resumed_global_seq: resumed.global_seq, permission_global_seq: permission.global_seq, visible_and_enabled: true, approved_through_ui: true, completed: true, replay_preserved: true }
  output.events = (await request(`/task-runs/${run.id}/events?after_seq=0&limit=500`)).items.map(({ global_seq, seq, type, task_run_id, payload }) => ({ global_seq, seq, type, task_run_id, ...(type === 'permission.resolved' ? { decision: payload.decision, tool_call_id: payload.tool_call_id } : {}) }))
  output.all_checks_passed = true
  console.log(JSON.stringify(output, null, 2))
} finally {
  await writeFile(`${directory}/recovery-output.json`, JSON.stringify(output, null, 2))
  await app.close()
}
