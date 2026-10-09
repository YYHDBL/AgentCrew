import assert from 'node:assert/strict'
import { _electron as electron } from 'playwright-core'
import { cp, mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { randomUUID } from 'node:crypto'
import { DatabaseSync } from 'node:sqlite'

const directory = resolve('.artifacts', `memory-scope-race-${Date.now()}`)
await mkdir(`${directory}/data`, { recursive: true })
await cp('../backend/data/config.json', `${directory}/data/config.json`)
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`], executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const page = await app.firstWindow()
page.setDefaultTimeout(60000)
let db
const evidence = { directory, cases: [], requests: [], transport: 'CDP暂停并继续真实HTTP响应，保留原始状态、正文及响应头' }
let cdp
let held
let endpoint
const wait = async (read, accepts) => {
  const deadline = Date.now() + 90000
  while (Date.now() < deadline) {
    const value = await read()
    if (accepts(value)) return value
    await new Promise((done) => setTimeout(done, 50))
  }
  throw new Error('实际范围及分页状态未满足验收条件')
}
const request = async (method, path, body, owner = true) => {
  const value = await page.evaluate(async ({ method, path, body, owner }) => {
    const port = await window.agentcrew.getBackendPort()
    const token = await window.agentcrew.getToken()
    const identity = sessionStorage.getItem('agentcrew-identity')
    const response = await fetch(`http://127.0.0.1:${port}/api${path}`, { method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...(!owner && identity ? { 'X-AgentCrew-Identity': identity } : {}) }, body: body ? JSON.stringify(body) : undefined })
    return { status: response.status, value: (await response.json()).data }
  }, { method, path, body, owner })
  evidence.requests.push({ method, path, status: value.status })
  return value
}
const scope = 'workspace_id=office&agent_id=xiaowen'
const marker = `真实独立分页材料${randomUUID()}`
const panel = (name) => page.getByRole('tabpanel', { name, exact: true })
const settle = () => page.evaluate(async () => { await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
const select = async (workspace, agent) => {
  await page.getByLabel('档案工作区').selectOption(workspace)
  await wait(() => page.getByLabel('档案员工').inputValue(), (value) => value === agent)
  await wait(() => page.getByRole('tabpanel').getByText('正在读取实际记忆资料。', { exact: true }).count(), (value) => value === 0)
  await settle()
}
const release = async () => {
  const url = held.request.url
  const finished = page.waitForEvent('requestfinished', { predicate: (item) => item.url() === url })
  await cdp.send('Fetch.continueResponse', { requestId: held.requestId })
  held = null
  await finished
  await cdp.send('Fetch.disable')
  await settle()
}
const pause = async (path, button) => {
  endpoint = path
  await cdp.send('Fetch.enable', { patterns: [{ urlPattern: `*/api/memory/${path}?*agent_id=xiaowen*after=*`, requestStage: 'Response' }] })
  await button.click()
  await wait(() => held, Boolean)
  assert.equal(held.responseStatusCode, 200)
  return held.request.url
}
try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  db = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
  const settings = (await request('GET', '/settings')).value
  assert.equal((await request('PATCH', '/settings', { memory: { ...settings.memory, soul_quota: 20000 } })).status, 200)
  for (let index = 0; index < 55; index++) {
    const store = (await request('GET', `/memory/stores/soul/xiaowen?${scope}`)).value
    assert.equal((await request('POST', `/memory/stores/soul/xiaowen?${scope}`, { change_id: randomUUID(), expected_revision: store.revision,
      text: `${marker}条目${index}`, basis: '所有者创建独立真实分页范围材料' })).status, 200)
    assert.equal((await request('POST', '/memory/skills', { change_id: randomUUID(), expected_revision: 0, workspace_id: 'office', agent_id: 'xiaowen',
      name: `分页核查技能${String(index).padStart(2, '0')}`, description: '核查真实分页资料', text: `${marker}技能${index}`, basis: '所有者创建独立真实技能材料' })).status, 200)
  }
  for (let index = 0; index < 55; index++) {
    await wait(() => db.prepare("SELECT count(*) AS n FROM memory_jobs WHERE status IN ('queued','running','waiting_approval','cancelling')").get().n, (count) => count === 0)
    assert.equal((await request('POST', '/memory/curate/run', { workspace_id: 'office', agent_id: 'xiaowen', client_request_id: randomUUID() })).status, 202)
  }
  await wait(() => db.prepare("SELECT count(*) AS n FROM memory_jobs WHERE status IN ('queued','running','waiting_approval','cancelling')").get().n, (count) => count === 0)
  await page.evaluate(() => { sessionStorage.setItem('workspace-id', 'office'); sessionStorage.setItem('agent-id', 'xiaowen') })
  await page.reload()
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  await page.getByRole('button', { name: '数字员工管理', exact: true }).click()
  await wait(() => page.getByLabel('档案员工').inputValue(), (value) => value === 'xiaowen')
  cdp = await page.context().newCDPSession(page)
  cdp.on('Fetch.requestPaused', (event) => {
    assert.equal(new URL(event.request.url).pathname, `/api/memory/${endpoint}`)
    held = event
  })
  const rows = (name, selector) => panel(name).locator(selector).allTextContents()
  for (const test of [
    { name: '账本分页', tab: '员工 soul', path: 'ledger', button: '更多变更记录', selector: '.memory-ledger article', ledger: true },
    { name: '条目分页', tab: '员工 soul', path: 'stores/soul/xiaowen', button: '更多条目', selector: '.memory-index button' },
    { name: '后台作业分页', tab: '员工 soul', path: 'jobs', button: '更多后台作业', selector: '.memory-job' },
    { name: '检索分页', tab: '员工 soul', path: 'search', button: '更多检索结果', selector: '.memory-search details', search: true }
  ]) {
    await page.getByRole('tab', { name: test.tab, exact: true }).click()
    if (test.ledger) {
      await panel(test.tab).locator('.memory-index button').filter({ hasText: marker }).first().click()
      await panel(test.tab).getByRole('button', { name: '查看变更记录', exact: true }).click()
    }
    if (test.search) {
      await panel(test.tab).getByLabel('查询内容').fill(marker)
      await panel(test.tab).getByRole('button', { name: '搜索历史', exact: true }).click()
    }
    await panel(test.tab).getByRole('button', { name: test.button, exact: true }).waitFor()
    const url = await pause(test.path, panel(test.tab).getByRole('button', { name: test.button, exact: true }))
    await select('analytics', 'xiaogang')
    const before = await rows(test.tab, test.selector)
    await release()
    const after = await rows(test.tab, test.selector)
    evidence.cases.push({ name: test.name, url, before, after })
    assert.deepEqual(after, before, `${test.name}迟到响应写入其他范围`)
    await select('office', 'xiaowen')
  }
  await page.getByRole('tab', { name: '员工 soul', exact: true }).click()
  await panel('员工 soul').getByRole('button', { name: '更多变更记录', exact: true }).waitFor()
  const abaUrl = await pause('ledger', panel('员工 soul').getByRole('button', { name: '更多变更记录', exact: true }))
  await select('analytics', 'xiaogang')
  await select('office', 'xiaowen')
  const beforeAba = await rows('员工 soul', '.memory-ledger article')
  assert.equal(beforeAba.length, 50)
  await release()
  const afterAba = await rows('员工 soul', '.memory-ledger article')
  evidence.cases.push({ name: '账本A至B再返回A', url: abaUrl, before: beforeAba, after: afterAba })
  assert.deepEqual(afterAba, beforeAba, '旧请求不能进入返回A后的新代次')
  await panel('员工 soul').getByRole('button', { name: '更多变更记录', exact: true }).click()
  await wait(() => panel('员工 soul').locator('.memory-ledger article').count(), (count) => count === 55)
  evidence.cases.push({ name: '当前授权范围正常分页', before_rows: 50, after_rows: 55 })

  await page.getByRole('button', { name: '技能管理', exact: true }).click()
  const skillsUrl = await pause('skills', page.getByRole('button', { name: '更多技能', exact: true }))
  await page.getByRole('button', { name: '数字员工管理', exact: true }).click()
  await page.getByRole('tab', { name: '员工 soul', exact: true }).click()
  await select('analytics', 'xiaogang')
  const beforeSkills = await rows('员工 soul', '.memory-index button')
  await release()
  const afterSkills = await rows('员工 soul', '.memory-index button')
  evidence.cases.push({ name: '技能分页离开页面后的迟到响应', url: skillsUrl, before: beforeSkills, after: afterSkills })
  assert.deepEqual(afterSkills, beforeSkills)
  await select('office', 'xiaowen')

  await page.getByRole('button', { name: '技能管理', exact: true }).click()
  await page.getByRole('button', { name: '新建技能', exact: true }).click()
  const skillName = `实际保存核查${randomUUID().slice(0, 8)}`
  await page.getByLabel('技能名称', { exact: true }).fill(skillName)
  await page.getByLabel('技能描述', { exact: true }).fill('核查真实保存与后续编辑')
  await page.getByLabel('技能正文', { exact: true }).fill('依据原始材料核查真实结果。')
  await page.getByLabel('保存依据', { exact: true }).fill('所有者通过实际界面创建技能')
  await page.getByRole('button', { name: '保存技能', exact: true }).click()
  await page.getByText('技能已保存', { exact: true }).waitFor()
  await page.getByRole('heading', { name: skillName, exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: '编辑条目', exact: true }).isEnabled(), true)
  evidence.cases.push({ name: '实际新建技能提示、正文选中及后续编辑', skill_name: skillName })
  await page.getByRole('button', { name: '数字员工管理', exact: true }).click()

  const second = (await request('POST', '/agents', { change_id: randomUUID(), expected_revision: 0, workspace_id: 'office', name: '撤权后仍可见员工',
    spec: { position: '核查真实分页撤权边界', model_slot: 'main', skill_ids: [], connector_ids: [] } })).value.id
  assert.equal((await request('POST', '/grants', { change_id: randomUUID(), resource_type: 'agent', resource_id: second, grantee_type: 'user', grantee_id: 'lilei' })).status, 200)
  assert.equal((await request('POST', `/memory/stores/soul/${second}?workspace_id=office&agent_id=${second}`, { change_id: randomUUID(), expected_revision: 0,
    text: '当前已授权员工材料', basis: '所有者创建独立范围对照材料' })).status, 200)
  await page.getByLabel('演示身份').selectOption('lilei')
  await page.getByRole('tab', { name: '员工 soul', exact: true }).click()
  await panel('员工 soul').locator('.memory-index button').filter({ hasText: marker }).first().click()
  await panel('员工 soul').getByRole('button', { name: '查看变更记录', exact: true }).click()
  const revokedUrl = await pause('ledger', panel('员工 soul').getByRole('button', { name: '更多变更记录', exact: true }))
  const grant = (await request('GET', '/grants?workspace_id=office&grantee_id=lilei&limit=200')).value.items.find((row) => row.resource_type === 'agent' && row.resource_id === 'xiaowen' && !row.revoked_at)
  assert.equal((await request('DELETE', `/grants/${grant.id}`, { change_id: randomUUID(), expected_revision: grant.revision })).status, 200)
  await wait(() => page.getByLabel('档案员工').inputValue(), (value) => value === second)
  await panel('员工 soul').locator('.memory-index button').filter({ hasText: '当前已授权员工材料' }).waitFor()
  await release()
  const privateRows = await panel('员工 soul').locator('.memory-ledger article').filter({ hasText: marker }).count()
  const forbidden = await request('GET', `/memory/stores/soul/xiaowen?${scope}`, undefined, false)
  evidence.cases.push({ name: '真实member撤权后迟到分页', url: revokedUrl, private_rows: privateRows, new_read_status: forbidden.status })
  assert.equal(privateRows, 0)
  assert.equal(forbidden.status, 403)
  evidence.all_checks_passed = true
  console.log(JSON.stringify({ directory, cases: evidence.cases.length, all_checks_passed: true }))
} finally {
  if (cdp) await cdp.send('Fetch.disable')
  await app.close()
  if (db) {
    evidence.event_watermark = db.prepare('SELECT max(global_seq) AS n FROM run_events').get().n
    evidence.audit_sequence = db.prepare('SELECT max(seq) AS n FROM audit_log').get().n
    db.close()
  }
  await writeFile(`${directory}/result.json`, JSON.stringify(evidence, null, 2))
}
