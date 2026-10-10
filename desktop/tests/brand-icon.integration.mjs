import assert from 'node:assert/strict'
import { cp, mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { _electron as electron } from 'playwright-core'

const directory = resolve('.artifacts', `brand-icon-${Date.now()}`)
await mkdir(`${directory}/data`, { recursive: true })
await cp('../backend/data/config.json', `${directory}/data/config.json`)
const source = resolve('assets/brand/agentcrew-icon.png')
const emitted = (await readdir('out/main/chunks')).find((name) => name.startsWith('agentcrew-icon-') && name.endsWith('.png'))
assert.ok(emitted, '构建产物缺少应用图标')
const bundled = resolve('out/main/chunks', emitted)
assert.equal(Buffer.compare(await readFile(source), await readFile(bundled)), 0)
execFileSync('iconutil', ['-c', 'iconset', 'assets/brand/agentcrew.icns', '-o', `${directory}/AgentCrew.iconset`])
const iconFiles = [16, 32, 128, 256, 512].flatMap((width) => [`icon_${width}x${width}.png`, `icon_${width}x${width}@2x.png`])
const iconBuffers = await Promise.all(iconFiles.map(async (file) => (await readFile(`${directory}/AgentCrew.iconset/${file}`)).toString('base64')))
const app = await electron.launch({ args: ['.', `--user-data-dir=${directory}`], executablePath: resolve('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron') })
const child = app.process()
const page = await app.firstWindow()
page.setDefaultTimeout(30000)
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
try {
  await page.getByText('任务服务已连接', { exact: true }).waitFor()
  const native = await app.evaluate(({ app, nativeImage }, paths) => {
    const image = nativeImage.createFromPath(paths.bundled)
    const size = image.getSize()
    const pixels = image.toBitmap()
    const alpha = (x, y) => pixels[(y * size.width + x) * 4 + 3]
    const sizes = paths.iconBuffers.map((data) => nativeImage.createFromBuffer(Buffer.from(data, 'base64')).getSize())
    return { empty: image.isEmpty(), size, visible: app.dock?.isVisible(), cornerAlpha: [alpha(0, 0), alpha(size.width - 1, 0), alpha(0, size.height - 1), alpha(size.width - 1, size.height - 1)], centreAlpha: alpha(size.width / 2, size.height / 2), sizes }
  }, { bundled, iconBuffers })
  assert.equal(native.empty, false)
  assert.deepEqual(native.size, { width: 1024, height: 1024 })
  assert.equal(native.visible, true)
  assert.deepEqual(native.cornerAlpha, [0, 0, 0, 0])
  assert.ok(native.centreAlpha > 0, '图标主体必须可见')
  const expected = [16, 32, 128, 256, 512].flatMap((width) => [{ width, height: width }, { width: width * 2, height: width * 2 }])
  assert.deepEqual(native.sizes, expected)
  await page.locator('.nav-brand img').waitFor()
  assert.equal(await page.locator('.nav-brand img').evaluate((image) => image.complete && image.naturalWidth === 1024 && image.naturalHeight === 1024), true)
  assert.equal(await page.locator('.empty-symbol img').evaluate((image) => image.complete && image.currentSrc === document.querySelector('.nav-brand img').currentSrc), true)
  assert.ok((await page.locator('link[rel="icon"]').getAttribute('href')).includes('agentcrew-icon-'))
  assert.deepEqual(errors, [])
  await app.evaluate(() => globalThis.agentcrewLifecycle.requestExit('menu'))
  await page.getByRole('dialog', { name: '退出 AgentCrew', exact: true }).waitFor()
  const closed = app.waitForEvent('close')
  await page.getByRole('button', { name: '确认退出', exact: true }).click()
  await closed
  await writeFile(`${directory}/result.json`, JSON.stringify({ passed: true, native, cases: ['图标随主进程构建产物保存', '真实 Electron 启动与 Dock 可见', 'PNG 尺寸与透明通道', 'ICNS 十种尺寸解码', '侧栏与欢迎界面图片加载', '窗口 favicon 资源', '正常退出'], errors }, null, 2))
  console.log(`应用图标检查通过：${directory}/result.json`)
} finally {
  if (child.exitCode === null) child.kill('SIGTERM')
}
