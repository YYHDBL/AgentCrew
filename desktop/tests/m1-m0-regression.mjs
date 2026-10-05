import assert from 'node:assert/strict'
import { _electron as electron } from 'playwright-core'
import { mkdir, cp, readFile, writeFile, stat } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { resolve, basename } from 'node:path'
import { createHash } from 'node:crypto'
import { DatabaseSync } from 'node:sqlite'

// 真实中断保留旧审批事件，再通过界面恢复并批准同一任务的新审批。
const directory = resolve('.artifacts', `m1-m0-${Date.now()}`)
await mkdir(directory, { recursive: true })
await cp(resolve('.artifacts/m1-memory-1791019192645/data'), `${directory}/data`, { recursive: true,
  filter: (path) => !['instance.lock', 'requests.jsonl', 'streams.jsonl', 'main-streams.jsonl', 'service.log'].includes(basename(path)) })
execFileSync(resolve('../backend/.venv/bin/python'), [resolve('../scripts/seed/m1_memory_data.py'),
  '--data-dir', directory, '--output', `${directory}/materials.json`])
const materials = JSON.parse(await readFile(`${directory}/materials.json`, 'utf8'))
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex')
const signature = async (path) => ({ sha256: sha(await readFile(path)), mtime_ns: String((await stat(path, { bigint: true })).mtimeNs) })
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`], executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const page = await app.firstWindow()
page.setDefaultTimeout(180000)
const output = { directory, operations: [], screenshots: [] }
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
  const conversation = await waitFor(() => page.evaluate(() => sessionStorage.getItem('conversation')), Boolean)
  const [run] = await waitFor(() => request(`/conversations/${conversation}/task-runs`), (runs) => runs.length > 0)
  const before = await waitFor(() => request(`/task-runs/${run.id}/events?after_seq=0&limit=500`), (history) => history.items.some((event) => ['permission.requested', 'run.failed', 'run.completed'].includes(event.type)))
  assert.ok(before.items.some((event) => event.type === 'permission.requested'), JSON.stringify(before.items.filter((event) => event.type.startsWith('run.'))))
  await page.getByRole('button', { name: '允许', exact: true }).first().waitFor()
  const oldPort = await page.evaluate(() => window.agentcrew.getBackendPort())
  const processLine = execFileSync('ps', ['-axo', 'pid,command'], { encoding: 'utf8' }).split('\n').find((line) => /\/python(?:3(?:\.12)?)? -m agentcrew_server/.test(line) && line.includes(directory))
  assert.ok(processLine)
  const pid = Number(processLine.trim().split(/\s+/)[0])
  process.kill(pid, 'SIGKILL')
  await page.getByRole('button', { name: '恢复', exact: true }).waitFor()
  await waitFor(() => page.evaluate(() => window.agentcrew.getBackendPort()), (port) => port && port !== oldPort)
  assert.equal(await page.locator('.approval').count(), 0)
  assert.equal((await request(`/conversations/${conversation}/task-runs`))[0].status, 'interrupted')
  const interruptedEvents = await request(`/task-runs/${run.id}/events?after_seq=0&limit=500`)
  const oldPermissions = interruptedEvents.items.filter((event) => event.type === 'permission.requested')
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
  const expiredApproval = page.locator('.process-card').filter({ has: page.getByRole('heading', { name: '审批已失效', exact: true }) })
  await expiredApproval.first().waitFor({ timeout: 10000 })
  assert.equal(await expiredApproval.count(), oldPermissions.length)
  assert.equal(await expiredApproval.getByRole('button').count(), 0)
  const activeApprovals = await page.locator('.approval').count()
  assert.ok(activeApprovals >= 1)
  await page.reload()
  await expiredApproval.first().waitFor()
  assert.equal(await expiredApproval.getByRole('button').count(), 0)
  await page.locator('.approval').first().waitFor()
  assert.equal(await page.locator('.approval').count(), activeApprovals)
  output.expired_approval = { tool_call_ids: oldPermissions.map((event) => event.payload.tool_call_id), historical_cards: oldPermissions.length, historical_card_visible: true, buttons: 0, active_approvals: activeApprovals, replay_preserved: true }
  const approval = page.locator('.approval').first().getByRole('button', { name: '允许', exact: true })
  await approval.waitFor()
  assert.equal(await approval.isEnabled(), true)
  const terminal = await waitFor(async () => {
    if (await approval.count() && await approval.isEnabled()) await approval.click()
    return request(`/conversations/${conversation}/task-runs`)
  }, (runs) => ['completed', 'failed'].includes(runs[0].status))
  assert.equal(terminal[0].status, 'completed')
  const afterApproval = await request(`/task-runs/${run.id}/events?after_seq=0&limit=500`)
  assert.ok(afterApproval.items.some((event) => event.type === 'permission.resolved' && event.payload.tool_call_id === permission.payload.tool_call_id && event.payload.decision === 'allow_once'))
  const scope = await request(`/conversations/${conversation}/scope`)
  assert.equal((await readFile(`${scope.workspace_dir}/c11-recovery/recovered.txt`, 'utf8')).trim(), 'C11 recovery completed')
  const recoveredPath = `${scope.workspace_dir}/c11-recovery/recovered.txt`
  const fileBeforeRefresh = await signature(recoveredPath)
  await page.reload()
  await page.getByRole('heading', { name: '已恢复 · 第 2 次尝试', exact: true }).waitFor()
  assert.equal(await page.locator('.approval').count(), 0)
  assert.equal(await page.getByRole('button', { name: '恢复', exact: true }).count(), 0)
  assert.deepEqual(await signature(recoveredPath), fileBeforeRefresh)
  output.recovered_file = { path: recoveredPath, before_refresh: fileBeforeRefresh, after_refresh: await signature(recoveredPath) }
  assert.deepEqual(errors, [])
  output.resumed_approval = { task_run_id: run.id, resumed_global_seq: resumed.global_seq, permission_global_seq: permission.global_seq, visible_and_enabled: true, approved_through_ui: true, completed: true, replay_preserved: true }
  output.events = (await request(`/task-runs/${run.id}/events?after_seq=0&limit=500`)).items.map(({ global_seq, seq, type, task_run_id, payload }) => ({ global_seq, seq, type, task_run_id, ...(type === 'permission.resolved' ? { decision: payload.decision, tool_call_id: payload.tool_call_id } : {}) }))
  assert.ok(!output.events.some((event) => event.type === 'permission.resolved' && oldPermissions.some((old) => old.payload.tool_call_id === event.tool_call_id)))
  output.operations.push('sigkill-approval-refresh-resume')
  await page.screenshot({ path: `${directory}/recovery-completed.png` })
  output.screenshots.push(`${directory}/recovery-completed.png`)
  const send = async (text, name = '发送任务') => {
    await page.getByRole('textbox', { name: '任务指令', exact: true }).fill(text)
    await page.getByRole('button', { name, exact: true }).click()
  }
  await page.getByRole('button', { name: '＋ 新建任务', exact: true }).click()
  await send('必须实际调用 ask_user 工具提问“贯穿排队验收等待”，等待我的回答。')
  await page.getByRole('textbox', { name: '问题回答', exact: true }).waitFor()
  const queuedConversation = await page.evaluate(() => sessionStorage.getItem('conversation'))
  await send('必须实际调用 ask_user 工具提问“贯穿队首已经继续”，等待回答后直接回复收到的回答。', '加入队列')
  await send('只回复“贯穿剩余指令”。', '加入队列')
  await page.getByText('排队中 · 2 条', { exact: true }).waitFor()
  await page.getByRole('button', { name: '停止任务', exact: true }).click()
  await page.getByText('队列已暂停 · 2 条', { exact: true }).waitFor()
  await page.reload()
  await page.getByText('队列已暂停 · 2 条', { exact: true }).waitFor()
  await page.getByRole('button', { name: '继续队列', exact: true }).click()
  await page.getByRole('textbox', { name: '问题回答', exact: true }).waitFor()
  await page.getByRole('button', { name: '取消剩余', exact: true }).click()
  await page.getByRole('textbox', { name: '问题回答', exact: true }).fill('M1 真实队列验收回答')
  await page.getByRole('button', { name: '提交回答', exact: true }).click()
  const queuedRuns = await waitFor(() => request(`/conversations/${queuedConversation}/task-runs`),
    (runs) => runs.length === 2 && runs.some((run) => run.status === 'completed'))
  assert.ok(queuedRuns.some((run) => run.status === 'cancelled'))
  const queueState = await request(`/conversations/${queuedConversation}/state`)
  assert.equal(queueState.queue.length, 0)
  await page.reload()
  await waitFor(() => request(`/conversations/${queuedConversation}/state`), (state) => state.queue.length === 0)
  output.queue = { conversation_id: queuedConversation, tasks: queuedRuns, state: queueState }
  output.operations.push('question-queue-stop-continue-cancel-refresh')
  await page.screenshot({ path: `${directory}/queue-completed.png` })
  output.screenshots.push(`${directory}/queue-completed.png`)
  const imported = await page.evaluate(async (files) => {
    const port = await window.agentcrew.getBackendPort()
    const token = await window.agentcrew.getToken()
    const response = await fetch(`http://127.0.0.1:${port}/api/conversations`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction: '请直接确认已经收到真实材料，保持材料文件完整，省略工具调用和记忆保存。',
        import_files: files, client_request_id: crypto.randomUUID() }) })
    if (response.status !== 201) throw new Error(`材料导入 HTTP ${response.status}`)
    return (await response.json()).data
  }, materials.files.map((file) => file.path))
  assert.equal(imported.materials.length, 20)
  assert.equal(imported.materials.filter((file) => file.error === null).length, 20)
  for (const file of materials.files) {
    const actual = await signature(file.path)
    assert.equal(actual.sha256, file.sha256)
    assert.equal(actual.mtime_ns, String(file.mtime_ns))
    const copy = `${imported.scope.materials_dir}/${basename(file.path)}`
    assert.equal((await signature(copy)).sha256, file.sha256)
  }
  await page.reload()
  await page.locator('.recent-tasks button').first().click()
  const materialRuns = await waitFor(async () => {
    const approval = page.locator('.approval').getByRole('button', { name: '允许', exact: true }).first()
    if (await approval.count() && await approval.isEnabled()) await approval.click()
    return request(`/conversations/${imported.conversation.id}/task-runs`)
  }, (runs) => ['completed', 'failed'].includes(runs[0].status))
  assert.equal(materialRuns[0].status, 'completed')
  for (const file of materials.files) {
    assert.equal((await signature(file.path)).sha256, file.sha256)
    assert.equal((await signature(`${imported.scope.materials_dir}/${basename(file.path)}`)).sha256, file.sha256)
  }
  const partial = await page.evaluate(async (files) => {
    const port = await window.agentcrew.getBackendPort()
    const token = await window.agentcrew.getToken()
    const response = await fetch(`http://127.0.0.1:${port}/api/conversations`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction: '请直接确认材料导入的实际结果，保持原始材料完整，省略工具调用和记忆保存。',
        import_files: files, client_request_id: crypto.randomUUID() }) })
    if (response.status !== 201) throw new Error(`部分材料导入 HTTP ${response.status}`)
    return (await response.json()).data
  }, [materials.files[0].path, `${directory}/office-materials/不存在的文件.txt`])
  assert.equal(partial.materials[0].error, null)
  assert.ok(partial.materials[1].error)
  await page.reload()
  await page.locator('.recent-tasks button').first().click()
  const partialRuns = await waitFor(async () => {
    const approval = page.locator('.approval').getByRole('button', { name: '允许', exact: true }).first()
    if (await approval.count() && await approval.isEnabled()) await approval.click()
    return request(`/conversations/${partial.conversation.id}/task-runs`)
  }, (runs) => ['completed', 'failed'].includes(runs[0].status))
  assert.equal(partialRuns[0].status, 'completed')
  await page.getByText(/导入失败：/).waitFor()
  output.materials = { conversation_id: imported.conversation.id, task_run_id: imported.task_run_id,
    imports: imported.materials, originals: materials.files, partial }
  output.operations.push('twenty-real-materials-partial-failure')
  await page.screenshot({ path: `${directory}/materials-imported.png` })
  output.screenshots.push(`${directory}/materials-imported.png`)
  const db = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
  output.queries = ['SELECT MAX(global_seq) AS event_watermark FROM run_events',
    'SELECT id,status,current_attempt_no FROM task_runs ORDER BY created_at',
    'SELECT task_run_id,COUNT(*) AS steps FROM steps GROUP BY task_run_id',
    'SELECT steps.task_run_id,COUNT(*) AS calls FROM llm_calls JOIN steps ON steps.id=llm_calls.step_id GROUP BY steps.task_run_id',
    'SELECT task_run_id,type,COUNT(*) AS events FROM run_events GROUP BY task_run_id,type',
    'PRAGMA foreign_key_check'].map((sql) => ({ sql, rows: db.prepare(sql).all() }))
  const writeLedger = db.prepare("SELECT call_id,status,dispatched_at,completed_at,input_hash FROM tool_calls WHERE task_run_id=? AND tool_name='write_file' AND dispatched_at IS NOT NULL").all(run.id)
  assert.equal(writeLedger.length, 1)
  assert.equal(writeLedger[0].status, 'completed')
  for (const type of ['tool.dispatched', 'tool.completed']) {
    assert.equal(db.prepare("SELECT COUNT(*) AS count FROM run_events WHERE task_run_id=? AND type=? AND json_extract(payload,'$.call_id')=?")
      .get(run.id, type, writeLedger[0].call_id).count, 1)
  }
  output.write_file_ledger = writeLedger
  const projections = db.prepare("SELECT t.id,(SELECT COUNT(*) FROM steps s WHERE s.task_run_id=t.id) AS steps,"
    + "(SELECT COUNT(*) FROM run_events e WHERE e.task_run_id=t.id AND e.type='step.started') AS events,"
    + "(SELECT COUNT(*) FROM llm_calls l JOIN steps s ON s.id=l.step_id WHERE s.task_run_id=t.id) AS calls,"
    + "(SELECT COUNT(*) FROM run_events e WHERE e.task_run_id=t.id AND e.type='llm.request_started') AS call_events FROM task_runs t").all()
  assert.ok(projections.every((row) => row.steps === row.events && row.calls === row.call_events))
  output.projection_checks = projections
  db.close()
  assert.deepEqual(errors, [])
  output.all_checks_passed = true
  console.log(JSON.stringify(output, null, 2))
} finally {
  await writeFile(`${directory}/recovery-output.json`, JSON.stringify(output, null, 2))
  await app.close()
}
