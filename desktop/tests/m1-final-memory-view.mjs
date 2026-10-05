import assert from 'node:assert/strict'
import { _electron as electron } from 'playwright-core'
import { cp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve, basename } from 'node:path'
import { DatabaseSync } from 'node:sqlite'

const seed = resolve(process.argv[2])
const source = JSON.parse(await readFile(resolve(process.argv[3]), 'utf8'))
assert.equal(source.all_checks_passed, true)
const reuse = process.argv.includes('--reuse')
const directory = reuse ? resolve(seed, '..') : resolve('.artifacts', `m1-final-view-${Date.now()}`)
if (reuse) assert.equal(resolve(directory, 'data'), seed)
await mkdir(directory, { recursive: true })
if (!reuse) await cp(seed, `${directory}/data`, { recursive: true,
  filter: (path) => !['instance.lock', 'requests.jsonl', 'streams.jsonl', 'main-streams.jsonl', 'service.log'].includes(basename(path)) })
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`],
  executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const page = await app.firstWindow()
page.setDefaultTimeout(60000)
const db = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
const output = { directory, screenshots: [], operations: [] }
try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  const conversation = await page.evaluate(async (id) => {
    const port = await window.agentcrew.getBackendPort()
    const token = await window.agentcrew.getToken()
    const response = await fetch(`http://127.0.0.1:${port}/api/conversations`, { headers: { Authorization: `Bearer ${token}` } })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return (await response.json()).data.find((row) => row.id === id)
  }, source.cross_date.conversation_id)
  const title = conversation.title || await page.evaluate((date) => `任务 ${new Date(date).toLocaleString('zh-CN')}`, conversation.last_activity_at)
  await page.locator('.recent-tasks button').filter({ hasText: title }).click()
  for (const [label, text, name] of [['USER 记忆', '用户喜欢以简洁中文汇报', 'user'],
    ['工作区记忆', '项目资料使用项目编号分类保存', 'workspace'],
    ['员工 soul', '处理资料时先确认用户提供的列顺序', 'soul']]) {
    await page.getByRole('button', { name: label, exact: true }).click()
    await page.getByText(text, { exact: true }).first().waitFor()
    await page.getByText(text, { exact: true }).first().click()
    await page.screenshot({ path: `${directory}/${name}.png` })
    output.screenshots.push(`${directory}/${name}.png`)
    output.operations.push(label)
  }
  const before = db.prepare('SELECT COUNT(*) AS count FROM llm_calls').get().count
  for (const text of ['资料', '项目资']) {
    await page.getByRole('textbox', { name: '查询内容', exact: true }).fill(text)
    const completed = page.waitForResponse((response) => {
      const url = new URL(response.url())
      return url.pathname === '/api/memory/search' && url.searchParams.get('query') === text
    })
    await page.getByRole('button', { name: '搜索历史', exact: true }).click()
    const response = await completed
    assert.equal(response.status(), 200)
    const hits = (await response.json()).data.items
    assert.ok(hits.length > 0)
    await page.waitForFunction((count) => document.querySelectorAll('.memory-search details').length === count, hits.length)
    await page.getByRole('button', { name: '搜索历史', exact: true }).waitFor({ state: 'visible' })
    output.operations.push(`中文检索：${text}`)
  }
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM llm_calls').get().count, before)
  await page.locator('.memory-search details').first().scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${directory}/chinese-search.png` })
  output.screenshots.push(`${directory}/chinese-search.png`)
  output.search_model_calls = { before, after: before }
  output.all_checks_passed = true
  console.log(JSON.stringify({ directory, operations: output.operations.length, all_checks_passed: true }))
} finally {
  db.close()
  await app.close()
  await writeFile(`${directory}/view-output.json`, JSON.stringify(output, null, 2))
}
