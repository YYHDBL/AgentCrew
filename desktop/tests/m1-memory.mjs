import assert from 'node:assert/strict'
import { _electron as electron } from 'playwright-core'
import { cp, mkdir, writeFile, readFile } from 'node:fs/promises'
import { resolve, basename } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { randomUUID } from 'node:crypto'

const directory = resolve('.artifacts', `m1-memory-${Date.now()}`)
await mkdir(directory, { recursive: true })
await cp(resolve('../data/m1-api-validation-01'), `${directory}/data`, { recursive: true,
  filter: (path) => !['instance.lock', 'requests.jsonl', 'streams.jsonl', 'main-streams.jsonl', 'service.log'].includes(basename(path)) })
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`],
  executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const page = await app.firstWindow()
page.setDefaultTimeout(120000)
await page.emulateMedia({ reducedMotion: 'reduce' })
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
const result = { directory, screenshots: [], operations: [] }
const data = (path) => page.evaluate(async (path) => {
  const port = await window.agentcrew.getBackendPort()
  const token = await window.agentcrew.getToken()
  const response = await fetch(`http://127.0.0.1:${port}/api${path}`, { headers: { Authorization: `Bearer ${token}` } })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return (await response.json()).data
}, path)
const waitFor = async (read, predicate, seconds = 240) => {
  const deadline = Date.now() + seconds * 1000
  while (Date.now() < deadline) {
    const value = await read()
    if (predicate(value)) return value
    await new Promise((done) => setTimeout(done, 20))
  }
  throw new Error('真实桌面状态未在期限内满足条件')
}
try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'USER 记忆', exact: true }).click()
  await page.getByRole('heading', { name: 'USER 记忆', exact: true }).waitFor()
  await page.getByRole('button', { name: '新建条目', exact: true }).click()
  await page.getByRole('textbox', { name: '记忆正文', exact: true }).fill('桌面核验偏好：需要清晰的中文报告。')
  await page.getByRole('textbox', { name: '保存依据', exact: true }).fill('所有者通过真实记忆界面填写的偏好。')
  await page.getByRole('button', { name: '保存记忆', exact: true }).click()
  await page.getByText('桌面核验偏好：需要清晰的中文报告。', { exact: true }).first().waitFor()
  result.operations.push('create-user')
  await page.getByRole('button', { name: '固定条目', exact: true }).click()
  await page.getByRole('button', { name: '取消固定', exact: true }).waitFor()
  await page.getByRole('button', { name: '归档条目', exact: true }).click()
  await page.getByRole('button', { name: '确认归档', exact: true }).click()
  await page.getByRole('button', { name: '恢复条目', exact: true }).waitFor()
  await page.getByRole('button', { name: '恢复条目', exact: true }).click()
  await page.getByRole('button', { name: '确认恢复', exact: true }).click()
  await page.getByRole('button', { name: '编辑条目', exact: true }).waitFor()
  result.operations.push('pin-archive-restore')
  await page.getByRole('button', { name: '编辑条目', exact: true }).click()
  await page.getByRole('textbox', { name: '记忆正文', exact: true }).fill('桌面核验偏好更新：需要保留完整来源。')
  await page.getByRole('textbox', { name: '保存依据', exact: true }).fill('所有者明确更新报告来源要求。')
  await page.getByRole('button', { name: '保存记忆', exact: true }).click()
  await page.getByRole('button', { name: '查看变更记录', exact: true }).click()
  await page.getByRole('button', { name: '恢复此次变更前内容', exact: true }).first().click()
  await page.getByRole('button', { name: '确认恢复变更', exact: true }).click()
  await page.getByText('桌面核验偏好：需要清晰的中文报告。', { exact: true }).first().waitFor()
  result.operations.push('ledger-restore')
  await page.getByRole('button', { name: '新建条目', exact: true }).click()
  await page.getByRole('textbox', { name: '记忆正文', exact: true }).fill('桌面财务核验：付款金额为 400 元。')
  await page.getByRole('textbox', { name: '保存依据', exact: true }).fill('所有者在界面明确填写的待审核财务事实。')
  await page.getByRole('button', { name: '保存记忆', exact: true }).click()
  await page.getByRole('button', { name: '审核通过', exact: true }).click()
  await page.getByRole('button', { name: '确认审核通过', exact: true }).click()
  await page.getByText('已通过人工审核', { exact: true }).waitFor()
  result.operations.push('risk-review')
  for (const [label, text] of [['工作区记忆', '桌面核验事实：材料保存在当前工作区。'], ['员工 soul', '桌面核验教训：先确认材料来源。']]) {
    await page.getByRole('button', { name: label, exact: true }).click()
    await page.getByRole('button', { name: '新建条目', exact: true }).click()
    await page.getByRole('textbox', { name: '记忆正文', exact: true }).fill(text)
    await page.getByRole('textbox', { name: '保存依据', exact: true }).fill('所有者通过真实界面填写的资料。')
    await page.getByRole('button', { name: '保存记忆', exact: true }).click()
    await page.getByText(text, { exact: true }).first().waitFor()
    result.operations.push(label)
  }
  await page.getByRole('button', { name: '技能管理', exact: true }).click()
  await page.getByRole('button', { name: '新建技能', exact: true }).click()
  await page.getByRole('textbox', { name: '技能名称', exact: true }).fill('桌面材料核验')
  await page.getByRole('textbox', { name: '技能描述', exact: true }).fill('核查真实材料的来源与编号')
  await page.getByRole('textbox', { name: '技能正文', exact: true }).fill('桌面材料核验\n读取材料，核对来源，再保存编号。')
  await page.getByRole('textbox', { name: '保存依据', exact: true }).fill('所有者明确创建的核验程序。')
  await page.getByRole('button', { name: '保存技能', exact: true }).click()
  await page.getByText('桌面材料核验', { exact: true }).first().waitFor()
  await page.getByRole('textbox', { name: '支撑文件路径', exact: true }).fill('references/checklist.md')
  await page.getByRole('textbox', { name: '支撑文件正文', exact: true }).fill('读取原始材料，核查编号和来源。')
  await page.getByRole('button', { name: '保存支撑文件', exact: true }).click()
  await page.getByRole('button', { name: 'references/checklist.md', exact: true }).waitFor()
  await page.getByRole('button', { name: 'references/checklist.md', exact: true }).click()
  assert.equal(await page.getByRole('textbox', { name: '支撑文件正文', exact: true }).inputValue(), '读取原始材料，核查编号和来源。')
  await waitFor(() => page.getByRole('button', { name: '保存支撑文件', exact: true }).isEnabled(), Boolean)
  await page.getByRole('textbox', { name: '支撑文件正文', exact: true }).fill('api_key=sk-owned-validation-12345678901234567890')
  await page.getByRole('button', { name: '保存支撑文件', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: '凭据' }).waitFor()
  await page.getByRole('textbox', { name: '支撑文件正文', exact: true }).fill('修正后完整材料：读取来源，核查编号。')
  await page.getByRole('button', { name: '保存支撑文件', exact: true }).click()
  await page.getByText('支撑文件已保存', { exact: true }).waitFor()
  result.operations.push('support-file-failure-corrected')
  result.operations.push('create-skill')
  await page.screenshot({ path: `${directory}/memory-skill.png` })
  result.screenshots.push(`${directory}/memory-skill.png`)
  await page.reload()
  await page.getByRole('heading', { name: '技能管理', exact: true }).waitFor()
  await page.getByText('桌面材料核验', { exact: true }).first().waitFor()
  await page.getByRole('button', { name: 'USER 记忆', exact: true }).click()
  await page.getByText('桌面核验偏好：需要清晰的中文报告。', { exact: true }).first().click()
  await page.getByText('桌面核验偏好：需要清晰的中文报告。', { exact: true }).first().waitFor()
  await page.getByRole('button', { name: '编辑条目', exact: true }).click()
  await page.getByRole('textbox', { name: '记忆正文', exact: true }).fill('必须保留的冲突草稿：核查所有来源。')
  assert.equal(await page.getByRole('combobox', { name: '条目状态', exact: true }).isDisabled(), true)
  await page.getByRole('textbox', { name: '保存依据', exact: true }).fill('所有者正在填写的冲突核验草稿。')
  await page.evaluate(async () => {
    const port = await window.agentcrew.getBackendPort()
    const token = await window.agentcrew.getToken()
    const base = `http://127.0.0.1:${port}/api/memory/stores/user/owner?workspace_id=default&agent_id=default`
    const store = (await (await fetch(base, { headers: { Authorization: `Bearer ${token}` } })).json()).data
    const response = await fetch(base, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ change_id: crypto.randomUUID(), expected_revision: store.revision, text: '真实并发编辑核验条目。', basis: '另一实际 HTTP 请求修改了记忆库。' }) })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
  })
  await page.getByRole('button', { name: '保存记忆', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: '预期修订' }).waitFor()
  assert.equal(await page.getByRole('textbox', { name: '记忆正文', exact: true }).inputValue(), '必须保留的冲突草稿：核查所有来源。')
  await page.getByRole('button', { name: '核查当前修订', exact: true }).click()
  await page.getByRole('button', { name: '采用当前修订', exact: true }).click()
  await page.getByRole('button', { name: '保存记忆', exact: true }).click()
  await page.getByText('必须保留的冲突草稿：核查所有来源。', { exact: true }).first().waitFor()
  result.operations.push('conflict-draft-retained')
  await page.getByRole('textbox', { name: '记忆正文', exact: true }).waitFor({ state: 'hidden' })
  await waitFor(() => page.getByRole('button', { name: '新建条目', exact: true }).isEnabled(), Boolean)
  await page.getByRole('button', { name: '新建条目', exact: true }).focus()
  await page.keyboard.press('Enter')
  await page.getByRole('textbox', { name: '记忆正文', exact: true }).waitFor()
  await waitFor(() => page.getByRole('textbox', { name: '记忆正文', exact: true }).evaluate((element) => element === document.activeElement), Boolean)
  await page.getByRole('button', { name: '取消编辑', exact: true }).click()
  await waitFor(() => page.getByRole('button', { name: '新建条目', exact: true }).evaluate((element) => element === document.activeElement), Boolean)
  result.operations.push('keyboard-focus')
  await page.screenshot({ path: `${directory}/memory-user.png` })
  result.screenshots.push(`${directory}/memory-user.png`)
  const counterDb = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
  const counter = counterDb.prepare('SELECT conversation_id,user_turns FROM memory_trigger_state ORDER BY user_turns DESC LIMIT 1').get()
  const conversation = (await data('/conversations')).find((item) => item.id === counter.conversation_id)
  const title = conversation.title || await page.evaluate((date) => `任务 ${new Date(date).toLocaleString('zh-CN')}`, conversation.last_activity_at)
  await page.getByRole('button', { name: '工作台', exact: true }).click()
  await page.locator('.recent-tasks button').filter({ hasText: title }).click()
  await page.getByRole('button', { name: 'USER 记忆', exact: true }).click()
  await page.getByRole('button', { name: '记忆设置', exact: true }).click()
  await page.getByRole('checkbox', { name: '后台写入需要人工审批', exact: true }).check()
  await page.getByRole('button', { name: '保存记忆设置', exact: true }).click()
  await page.getByText('记忆设置已保存', { exact: true }).waitFor()
  await page.getByRole('button', { name: '工作台', exact: true }).click()
  const target = (Math.floor(counter.user_turns / 10) + 1) * 10
  const marker = randomUUID().slice(0, 12)
  for (let index = counter.user_turns + 1; index <= target; index += 1) {
    const instruction = `长期偏好新增：每份报告必须注明桌面来源标识 ${marker}。这项偏好需要在任务完成后的后台检查保存到 USER。本次前台只用一句话确认收到 ${index} 次指令，直接文字回复。`
    await page.getByRole('textbox', { name: '任务指令', exact: true }).fill(instruction)
    await page.getByRole('button', { name: '发送任务', exact: true }).click()
    const run = await waitFor(() => data(`/conversations/${counter.conversation_id}/task-runs`), (rows) => rows.some((row) => row.instruction === instruction))
      .then((rows) => rows.find((row) => row.instruction === instruction))
    await waitFor(() => data(`/conversations/${counter.conversation_id}/task-runs`), (rows) => rows.some((row) => row.id === run.id && row.status === 'completed'))
    await waitFor(() => counterDb.prepare("SELECT status FROM memory_jobs WHERE task_run_id=? AND kind='summary'").get(run.id),
      (row) => row && ['completed', 'failed', 'cancelled', 'interrupted'].includes(row.status))
  }
  const job = await waitFor(() => counterDb.prepare("SELECT id,status FROM memory_jobs WHERE kind='memory_review' AND trigger_key=?").get(`${counter.conversation_id}:${target}`), (row) => row?.status === 'waiting_approval')
  const pending = await data(`/memory/jobs/${job.id}`)
  const approval = pending.approvals.find((item) => item.status === 'pending')
  await page.locator('.details-scroll .memory-approval').getByRole('button', { name: '本次允许', exact: true }).last().click()
  await waitFor(() => counterDb.prepare("SELECT COUNT(*) AS count FROM memory_ledger WHERE json_extract(source,'$.job_id')=?").get(job.id), (row) => row.count >= 1)
  const allowed = counterDb.prepare('SELECT status FROM memory_job_approvals WHERE id=?').get(approval.id)
  assert.equal(allowed.status, 'allowed')
  await page.getByRole('textbox', { name: '任务指令', exact: true }).fill('新的前台指令：停止本次后台继续执行，请直接确认已收到新的任务。')
  await page.getByRole('button', { name: '发送任务', exact: true }).click()
  const cancellingTask = await waitFor(() => data(`/conversations/${counter.conversation_id}/task-runs`),
    (rows) => rows.some((row) => row.instruction === '新的前台指令：停止本次后台继续执行，请直接确认已收到新的任务。'))
    .then((rows) => rows.find((row) => row.instruction === '新的前台指令：停止本次后台继续执行，请直接确认已收到新的任务。'))
  await waitFor(() => counterDb.prepare('SELECT status FROM memory_jobs WHERE id=?').get(job.id), (row) => ['cancelled', 'completed'].includes(row.status))
  await waitFor(() => data(`/conversations/${counter.conversation_id}/task-runs`), (rows) => rows.some((row) => row.id === cancellingTask.id && row.status === 'completed'))
  await page.getByRole('button', { name: 'USER 记忆', exact: true }).click()
  await page.getByText('记住了', { exact: true }).first().waitFor()
  await page.reload()
  await page.getByRole('heading', { name: 'USER 记忆', exact: true }).waitFor()
  await page.getByText('记住了', { exact: true }).first().waitFor()
  await waitFor(() => page.getByRole('button', { name: '新建条目', exact: true }).isEnabled(), Boolean)
  result.background_job = { job_id: job.id, approval_id: approval.id, approved_status: allowed.status,
    final_status: counterDb.prepare('SELECT status FROM memory_jobs WHERE id=?').get(job.id).status,
    source_conversation_id: counter.conversation_id,
    new_frontend_task_run_id: cancellingTask.id,
    ledger: counterDb.prepare("SELECT id,change_id,global_seq,source,after_text FROM memory_ledger WHERE json_extract(source,'$.job_id')=?").all(job.id) }
  result.operations.push('real-background-approval-notification-refresh')
  await page.screenshot({ path: `${directory}/memory-background.png` })
  result.screenshots.push(`${directory}/memory-background.png`)
  counterDb.close()
  const db = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
  result.queries = ['SELECT store_type,store_id,entry_hash,state,needs_review,text FROM memory_entries WHERE text LIKE \'桌面%\'',
    'SELECT id,store_type,action,global_seq,source FROM memory_ledger ORDER BY id DESC LIMIT 20'].map((sql) => ({ sql, rows: db.prepare(sql).all() }))
  db.close()
  assert.deepEqual(errors, [])
  result.all_checks_passed = true
  console.log(JSON.stringify({ directory, operations: result.operations.length, all_checks_passed: true }))
} finally {
  await writeFile(`${directory}/memory-output.json`, JSON.stringify(result, null, 2))
  await app.close()
}
