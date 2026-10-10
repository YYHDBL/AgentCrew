import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import { resolve } from 'node:path'
import { build } from 'esbuild'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const directory = resolve('.artifacts', 'markdown-check')
await mkdir(directory, { recursive: true })
const output = `${directory}/content.mjs`
await build({ entryPoints: ['src/renderer/src/MessageContent.tsx'], outfile: output, bundle: true, platform: 'node', format: 'esm', packages: 'external', jsx: 'automatic' })
const { MessageContent } = await import(pathToFileURL(output).href)
const render = (text) => renderToStaticMarkup(React.createElement(MessageContent, { text }))
const content = render('# 标题\n\n**关键内容**\n\n- 第一项\n- 第二项\n\n| 文件 | 状态 |\n| --- | --- |\n| 汇总.md | 完成 |\n\n```python\nprint("确认")\n```')
assert.ok(content.includes('<h1>标题</h1>'))
assert.ok(content.includes('<strong>关键内容</strong>'))
assert.ok(content.includes('<ul>'))
assert.ok(content.includes('<table>'))
assert.ok(content.includes('<th>文件</th>'))
assert.ok(content.includes('language-python'))
assert.ok(content.includes('复制代码'))
const unsafe = render('[危险链接](javascript:alert(1))\n\n<script>alert(1)</script>\n\n![图片](https://example.com/tracking.png)')
assert.equal(unsafe.includes('href="javascript:'), false)
assert.equal(unsafe.includes('<script>'), false)
assert.equal(unsafe.includes('<img'), false)
assert.ok(render('[文档](https://example.com/docs)').includes('复制链接：https://example.com/docs'))
console.log('Markdown 标题、列表、表格、代码块和内容边界检查通过')
