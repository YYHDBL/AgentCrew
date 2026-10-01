import assert from 'node:assert/strict'
import { chromium } from 'playwright-core'
import { readFile, writeFile } from 'node:fs/promises'

// 原生材料选择由桌面操作完成，此脚本只检查真实响应并保存证据。
const browser = await chromium.connectOverCDP('http://127.0.0.1:9229')
const page = browser.contexts()[0].pages()[0]
const result = await page.evaluate(async () => {
  const id = sessionStorage.getItem('conversation')
  const port = await window.agentcrew.getBackendPort()
  const token = await window.agentcrew.getToken()
  const get = async (path) => (await (await fetch(`http://127.0.0.1:${port}/api${path}`, { headers: { Authorization: `Bearer ${token}` } })).json()).data
  const runs = await get(`/conversations/${id}/task-runs`)
  return { id, scope: await get(`/conversations/${id}/scope`), state: await get(`/conversations/${id}/state`), events: await get(`/task-runs/${runs[0].id}/events?after_seq=0&limit=500`) }
})
assert.equal(result.state.state, 'idle')
assert.equal(await readFile(`${result.scope.materials_dir}/package.json`, 'utf8'), await readFile('package.json', 'utf8'))
assert.equal(result.scope.folders[0].access, 'read_write')
assert.ok(result.events.items.some((event) => event.type === 'materials.imported'))
await page.screenshot({ path: '../docs/acceptance/assets/C11/native-materials.png' })
await writeFile('../docs/acceptance/assets/C11/native-materials.json', JSON.stringify(result, null, 2))
console.log(JSON.stringify({ nativeMaterials: 'passed', conversation: result.id, scope: result.scope }))
await browser.close()
