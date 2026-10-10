import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import postcss from 'postcss'
import { FastColor } from '@ant-design/fast-color'

const css = postcss.parse(await readFile('src/renderer/src/styles.css', 'utf8'))
const declarations = (selector) => {
  const values = {}
  css.walkRules(selector, (rule) => rule.walkDecls((declaration) => { values[declaration.prop] = declaration.value }))
  return values
}
const luminance = (color) => {
  const { r, g, b } = new FastColor(color).toRgb()
  const linear = (value) => value / 255 <= 0.04045 ? value / 255 / 12.92 : ((value / 255 + 0.055) / 1.055) ** 2.4
  return linear(r) * 0.2126 + linear(g) * 0.7152 + linear(b) * 0.0722
}
const contrast = (foreground, background) => {
  const first = luminance(foreground)
  const second = luminance(background)
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}
const tokens = declarations(':root')
const selected = declarations('.recent-tasks button[aria-current]')
for (const background of [tokens['--surface'], tokens['--sidebar'], selected.background]) {
  assert.ok(contrast(tokens['--muted'], background) >= 4.5, '辅助文字必须在白色、侧栏和选中背景上保持足够对比度')
}
for (const selector of [
  '.task-status.status-completed',
  '.task-status.status-waiting_approval, .task-status.status-waiting_verification',
  '.task-status.status-failed, .task-status.status-interrupted',
  '.approval-heading .ant-tag, .artifact-card .ant-tag-gold',
  '.artifact-card .ant-tag-green, .tool-record .ant-tag-green',
  '.artifact-card .ant-tag-red, .tool-record .ant-tag-red',
  '.tool-record .ant-tag-blue'
]) {
  const colors = declarations(selector)
  assert.ok(colors.color && colors.background, `缺少状态颜色：${selector}`)
  assert.ok(contrast(colors.color, colors.background) >= 4.5, `状态文字对比度不足：${selector}`)
}
assert.ok(contrast(tokens['--accent'], tokens['--surface']) >= 3, '键盘焦点需要清楚可见')
console.log('工作台辅助文字、任务状态、审批、产物与焦点对比度检查通过')
