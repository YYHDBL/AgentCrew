import assert from 'node:assert/strict'
import { readFile, stat } from 'node:fs/promises'
import { resolve } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { DatabaseSync } from 'node:sqlite'

export async function governanceWorkflows({ page, directory, endpoint, upstream, request, waitFor, result }) {
  const runtime = await page.evaluate(async () => ({ port: await window.agentcrew.getBackendPort(), token: await window.agentcrew.getToken() }))
  const materialRoot = `${directory}/materials`
  execFileSync(resolve('../backend/.venv/bin/python'), [resolve('../scripts/seed/m2_governance_data.py'),
    '--url', `http://127.0.0.1:${runtime.port}`, '--data-dir', materialRoot, '--connector-url', endpoint],
  { env: { ...process.env, AGENTCREW_TOKEN: runtime.token }, stdio: 'pipe' })
  const seed = JSON.parse(await readFile(`${materialRoot}/seed-evidence.json`, 'utf8'))
  result.seed = seed
  const db = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
  const signature = async (path) => ({ path, sha256: createHash('sha256').update(await readFile(path)).digest('hex'),
    mtime_ns: String((await stat(path, { bigint: true })).mtimeNs) })
  const events = (id, type) => db.prepare('SELECT global_seq,type,payload FROM run_events WHERE task_run_id=? AND type=? ORDER BY global_seq').all(id, type)
  const calls = (id) => db.prepare('SELECT call_id,tool_name,input,status,dispatched_at FROM tool_calls WHERE task_run_id=? ORDER BY prepared_at').all(id)
  const denial = (run, target, reason) => {
    const matched = calls(run.task_run_id).filter((call) => call.tool_name === 'write_file' &&
      resolve(`${directory}/data/workspaces/office/files`, JSON.parse(call.input).path) === target)
    assert.ok(matched.length > 0)
    const failures = events(run.task_run_id, 'tool.failed').map((event) => ({ ...event, payload: JSON.parse(event.payload) }))
    const approvals = events(run.task_run_id, 'permission.requested').map((event) => JSON.parse(event.payload))
    const evidence = matched.map((call) => {
      assert.equal(call.dispatched_at, null)
      assert.ok(failures.some((event) => event.payload.call_id === call.call_id && event.payload.error === 'PERMISSION_DENIED'))
      assert.ok(!approvals.some((card) => card.tool_call_id === call.call_id))
      const audit = db.prepare('SELECT seq,action,resource_id,detail FROM audit_log WHERE resource_id=? AND action LIKE ?').all(call.call_id, `permission.denied:${reason}%`)
      assert.ok(audit.length > 0)
      return { call, failures: failures.filter((event) => event.payload.call_id === call.call_id), audit }
    })
    return evidence
  }
  const create = async (instruction, workspace = 'office', agent = 'xiaowen', folders = []) => {
    const created = await request('POST', '/conversations', { workspace_id: workspace, agent_id: agent, instruction, folders, client_request_id: randomUUID() })
    assert.equal(created.status, 201)
    result.workflow_tasks ??= []
    result.workflow_tasks.push({ instruction, ...created.value })
    await page.evaluate((id) => sessionStorage.setItem('conversation', id), created.value.conversation.id)
    await page.reload()
    await page.locator('.message.user').filter({ hasText: instruction }).waitFor()
    return created.value
  }
  const questions = async (run, answer) => {
    const pending = (await request('GET', `/conversations/${run.conversation.id}/questions`)).value
    for (const question of pending) assert.equal((await request('POST', `/questions/${question.request_id}/answer`, { answer })).status, 200)
  }
  const terminal = async (run, answer) => waitFor(async () => {
    await questions(run, answer)
    const pending = (await request('GET', `/task-runs/${run.task_run_id}/approvals`)).value
    for (const card of pending.filter((row) => !row.stale)) {
      assert.equal((await request('POST', `/tool-approvals/${card.call_id}`, { decision: 'reject_once', input_hash: card.input_hash })).status, 200)
    }
    return db.prepare('SELECT status FROM task_runs WHERE id=?').get(run.task_run_id).status
  }, (status) => ['completed', 'failed', 'cancelled'].includes(status), 240)
  const approval = async (run, target) => waitFor(async () => {
    await questions(run, `所有者已经授权本次合法文件操作。请使用write_file写入指定目标${target}，等待实际审批。`)
    const cards = (await request('GET', `/task-runs/${run.task_run_id}/approvals`)).value
    return cards.find((card) => !card.stale && calls(run.task_run_id).some((call) => call.call_id === card.call_id && call.tool_name === 'write_file' && JSON.parse(call.input).path.endsWith(target)))
  }, Boolean, 240)
  try {
    const rules = (await request('GET', '/agents/xiaowen/permission-rules')).value.items
    for (const rule of rules.filter((row) => !row.revoked_at && row.effect === 'allow' && row.tool_name === 'write_file')) {
      assert.equal((await request('DELETE', `/agents/xiaowen/permission-rules/${rule.id}`, { change_id: randomUUID(), expected_revision: rule.revision })).status, 200)
    }
    const firstTarget = 'm2-13/approved/first.txt'
    const first = await create(`仅调用write_file在当前工作区写入${firstTarget}，完整正文为M2 actual first write。等待文件审批，完成后依据真实工具结果说明。`)
    const firstCard = await approval(first, firstTarget)
    await page.locator('.approval').filter({ hasText: firstTarget }).getByRole('button', { name: '总是允许', exact: true }).click()
    await page.getByRole('dialog', { name: '确认永久权限规则', exact: true }).waitFor()
    await page.screenshot({ path: `${directory}/permanent-rule.png` })
    result.screenshots.push(`${directory}/permanent-rule.png`)
    await page.getByRole('button', { name: '确认保存永久规则', exact: true }).click()
    await waitFor(() => events(first.task_run_id, 'permission.resolved').some((event) => JSON.parse(event.payload).tool_call_id === firstCard.call_id), Boolean)
    await terminal(first, '请依据已经完成的真实文件写入简短说明结果。')
    const firstPath = `${directory}/data/workspaces/office/files/${firstTarget}`
    assert.equal(await readFile(firstPath, 'utf8'), 'M2 actual first write')
    const firstSignature = await signature(firstPath)
    const permanent = (await request('GET', '/agents/xiaowen/permission-rules')).value.items.find((rule) => rule.pattern === resolve(firstPath, '..') && !rule.revoked_at)
    assert.equal(permanent.effect, 'allow')
    const second = await create('仅调用write_file写入当前工作区m2-13/approved/second.txt，完整正文为M2 actual second write。完成后核查真实结果。')
    await terminal(second, '这是当前范围内的合法文件操作，请执行指定write_file并核查结果。')
    assert.equal(await readFile(`${directory}/data/workspaces/office/files/m2-13/approved/second.txt`, 'utf8'), 'M2 actual second write')
    assert.equal(events(second.task_run_id, 'permission.requested').filter((event) => JSON.parse(event.payload).tool === 'write_file').length, 0)
    const externalPath = `${materialRoot}/authorized-external/created.txt`
    const external = await create(`仅调用write_file写入已经授权目录的绝对路径${externalPath}，完整正文为M2 actual external write。等待审批后核查结果。`, 'office', 'xiaowen', [`${materialRoot}/authorized-external`])
    const externalCard = await approval(external, 'created.txt')
    await page.locator('.approval').filter({ hasText: externalPath }).getByRole('button', { name: '允许', exact: true }).click()
    await waitFor(() => events(external.task_run_id, 'permission.resolved').some((event) => JSON.parse(event.payload).tool_call_id === externalCard.call_id), Boolean)
    await terminal(external, '请依据已经批准的真实文件操作完成结果核查。')
    assert.equal(await readFile(externalPath, 'utf8'), 'M2 actual external write')
    const outsidePath = `${materialRoot}/outside-scope/source.txt`
    const outsideBefore = await signature(outsidePath)
    const outside = await create(`请调用write_file尝试写入绝对路径${outsidePath}，正文为M2 forbidden outside write。依据工具实际拒绝结果报告，禁止扩大scope或改变授权。`)
    await terminal(outside, '请核查工具实际范围拒绝结果，禁止扩大scope。')
    assert.deepEqual(await signature(outsidePath), outsideBefore)
    const outsideDenial = denial(outside, outsidePath, 'OUT_OF_SCOPE')
    result.cases.push({ name: '工作区永久允许、同目录后续写入、授权外部目录审批与scope外直接拒绝', passed: true,
      tasks: [first.task_run_id, second.task_run_id, external.task_run_id, outside.task_run_id], approvals: [firstCard, externalCard], permanent_rule: permanent,
      files: [firstSignature, await signature(externalPath), outsideBefore], outside_denial: outsideDenial })
    assert.equal((await request('POST', '/agents/xiaowen/permission-rules', { change_id: randomUUID(), tool_name: 'write_file', pattern: resolve(firstPath, '..'), effect: 'deny' })).status, 200)
    const denied = await create(`请调用write_file尝试将当前工作区${firstTarget}修改为M2 forbidden deny write。根据实际deny结果报告，禁止修改规则或扩大授权。`)
    await terminal(denied, '请保留当前deny边界，核查write_file的实际拒绝结果。')
    assert.deepEqual(await signature(firstPath), firstSignature)
    const ruleDenial = denial(denied, firstPath, 'deny 规则命中')
    result.cases.push({ name: 'deny优先于已有allow，直接拒绝且文件SHA和mtime保持一致', passed: true, task_run_id: denied.task_run_id, file: firstSignature, denial: ruleDenial })
    const beforeHttp = upstream.prepare('SELECT count(*) AS count FROM requests').get().count
    const absent = await create(`核查当前工具是否提供http_request，连接器${seed.connector.id}的地址为${endpoint}/not-authorized。仅报告当前能力与授权边界；没有HTTP授权时禁止使用bash或MCP网络绕过。`, 'analytics', 'xiaogang')
    await terminal(absent, '请依据当前工具声明报告真实能力，不要扩大授权。')
    const declarations = events(absent.task_run_id, 'llm.request_started').map((event) => JSON.parse(event.payload))
    assert.ok(declarations.length && declarations.every((event) => !event.tool_names.includes('http_request')))
    assert.equal(upstream.prepare('SELECT count(*) AS count FROM requests').get().count, beforeHttp)
    result.cases.push({ name: '小刚实际模型声明无HTTP能力且持久化服务没有新增请求', passed: true, task_run_id: absent.task_run_id, declarations })
    const grant = (await request('GET', '/grants?workspace_id=office&grantee_id=xiaowen&limit=200')).value.items.find((row) => row.resource_id === seed.connector.id && !row.revoked_at)
    const waiting = await create(`仅使用http_request发送真实POST，参数为${JSON.stringify({ connector_id: seed.connector.id, url: `${endpoint}/revoked-write`, method: 'POST', body: 'M2 actual revoke boundary' })}，等待审批后报告真实结果。`)
    const waitingCards = await waitFor(async () => {
      await questions(waiting, '请按照原始指令调用指定http_request并等待实际审批。')
      return (await request('GET', `/task-runs/${waiting.task_run_id}/approvals`)).value.find((card) => !card.stale && calls(waiting.task_run_id).some((call) => call.call_id === card.call_id && call.tool_name === 'http_request'))
    }, Boolean, 240)
    const snapshotPath = `${directory}/data/conversations/${waiting.conversation.id}/memory-snapshot.json`
    const snapshotBefore = await signature(snapshotPath)
    const bindingBefore = db.prepare('SELECT agent_spec,skill_versions FROM task_governance WHERE task_run_id=?').get(waiting.task_run_id)
    const beforeRevoke = upstream.prepare('SELECT count(*) AS count FROM requests').get().count
    assert.equal((await request('DELETE', `/grants/${grant.id}`, { change_id: randomUUID(), expected_revision: grant.revision })).status, 200)
    assert.equal((await request('POST', `/tool-approvals/${waitingCards.call_id}`, { decision: 'allow_once', input_hash: waitingCards.input_hash })).status, 409)
    await terminal(waiting, '当前Grant已经撤销，禁止继续派发。')
    assert.equal(upstream.prepare('SELECT count(*) AS count FROM requests').get().count, beforeRevoke)
    assert.ok(calls(waiting.task_run_id).filter((call) => call.tool_name === 'http_request').every((call) => !call.dispatched_at))
    assert.deepEqual(await signature(snapshotPath), snapshotBefore)
    assert.deepEqual(db.prepare('SELECT agent_spec,skill_versions FROM task_governance WHERE task_run_id=?').get(waiting.task_run_id), bindingBefore)
    result.cases.push({ name: '等待HTTP审批期间Grant撤销，旧审批409、没有派发、冻结快照与版本完整', passed: true,
      task_run_id: waiting.task_run_id, grant, approval: waitingCards, snapshot: snapshotBefore, binding: bindingBefore })
    result.workflow_sql = ['SELECT id,status,current_attempt_no FROM task_runs ORDER BY created_at',
      'SELECT call_id,task_run_id,tool_name,status,dispatched_at FROM tool_calls ORDER BY prepared_at',
      'SELECT global_seq,type,task_run_id,payload FROM run_events ORDER BY global_seq',
      'SELECT seq,actor_id,action,resource_type,resource_id FROM audit_log ORDER BY seq'].map((sql) => ({ sql, rows: db.prepare(sql).all() }))
    await page.getByRole('button', { name: '治理管理', exact: true }).click()
    await page.getByRole('heading', { name: '员工与治理', exact: true }).waitFor()
  } finally {
    db.close()
  }
}
