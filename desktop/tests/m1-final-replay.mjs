import assert from 'node:assert/strict'
import { _electron as electron } from 'playwright-core'
import { readFile, writeFile, stat } from 'node:fs/promises'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'
import { DatabaseSync } from 'node:sqlite'

const directory = resolve(process.argv[2])
const taskId = process.argv[3]
const path = resolve(process.argv[4])
const signature = async () => ({ sha256: createHash('sha256').update(await readFile(path)).digest('hex'),
  mtime_ns: String((await stat(path, { bigint: true })).mtimeNs) })
const before = await signature()
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`],
  executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const page = await app.firstWindow()
page.setDefaultTimeout(60000)
const db = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
const result = { directory, task_run_id: taskId, file_before_restart: before }
try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  const run = db.prepare('SELECT conversation_id,status,current_attempt_no FROM task_runs WHERE id=?').get(taskId)
  assert.equal(run.status, 'completed')
  const conversation = await page.evaluate(async (id) => {
    const port = await window.agentcrew.getBackendPort()
    const token = await window.agentcrew.getToken()
    const response = await fetch(`http://127.0.0.1:${port}/api/conversations`, { headers: { Authorization: `Bearer ${token}` } })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return (await response.json()).data.find((row) => row.id === id)
  }, run.conversation_id)
  const title = conversation.title || await page.evaluate((date) => `任务 ${new Date(date).toLocaleString('zh-CN')}`, conversation.last_activity_at)
  await page.getByRole('button', { name: '工作台', exact: true }).click()
  await page.locator('.recent-tasks button').filter({ hasText: title }).click()
  await page.getByRole('heading', { name: '已恢复 · 第 2 次尝试', exact: true }).last().waitFor()
  assert.deepEqual(await signature(), before)
  await page.reload()
  await page.getByRole('heading', { name: '已恢复 · 第 2 次尝试', exact: true }).last().waitFor()
  assert.deepEqual(await signature(), before)
  const ledger = db.prepare("SELECT call_id,status,dispatched_at,completed_at,input_hash FROM tool_calls WHERE task_run_id=? AND tool_name='write_file' AND dispatched_at IS NOT NULL").all(taskId)
  assert.equal(ledger.length, 1)
  assert.equal(ledger[0].status, 'completed')
  const counts = ['tool.prepared', 'tool.dispatched', 'tool.completed'].map((type) => ({ type,
    count: db.prepare("SELECT COUNT(*) AS count FROM run_events WHERE task_run_id=? AND type=? AND json_extract(payload,'$.call_id')=?")
      .get(taskId, type, ledger[0].call_id).count }))
  assert.ok(counts.every((row) => row.count === 1))
  result.write_file_ledger = ledger
  result.actual_event_counts = counts
  result.file_after_restart_and_refresh = await signature()
  result.all_checks_passed = true
  await page.screenshot({ path: `${directory}/replay-stable.png` })
  console.log(JSON.stringify(result))
} finally {
  db.close()
  await app.close()
  await writeFile(`${directory}/replay-output.json`, JSON.stringify(result, null, 2))
}
