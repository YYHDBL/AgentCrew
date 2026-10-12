import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import openapiTS, { astToString } from 'openapi-typescript'
import { compile } from 'json-schema-to-typescript'
import Ajv from 'ajv'
import standaloneCode from 'ajv/dist/standalone/index.js'
import { build } from 'esbuild'

const check = process.argv.includes('--check')
const directory = new URL('../../src/renderer/src/api/generated/', import.meta.url)
const contract = new URL('../../../docs/contracts/openapi.yaml', import.meta.url)
const eventFile = new URL('../../../docs/contracts/events.schema.json', import.meta.url)
const eventText = await readFile(eventFile, 'utf8')
const ajv = new Ajv({ strict: false, allErrors: true, code: { source: true, esm: true, lines: true } })
const eventValidator = ajv.compile(JSON.parse(eventText))
const staticValidator = await build({ stdin: { contents: standaloneCode(ajv, eventValidator), loader: 'js',
  resolveDir: fileURLToPath(new URL('../../', import.meta.url)) }, bundle: true, write: false, platform: 'browser', format: 'esm' })
const outputs = [
  ['http.ts', astToString(await openapiTS(contract))],
  ['events.ts', await compile(JSON.parse(eventText), 'EventFrame', {
    cwd: fileURLToPath(new URL('../../../docs/contracts/', import.meta.url)),
    bannerComment: '/* 从正式事件契约生成，请运行 npm run gen:api。 */',
    unreachableDefinitions: true, unknownAny: true
  })],
  ['events.schema.json', eventText],
  ['event-validator.js', staticValidator.outputFiles[0].text],
  ['event-validator.d.ts', "import type { ValidateFunction } from 'ajv'\nimport type { EventFrame } from './events'\ndeclare const validate: ValidateFunction<EventFrame>\nexport default validate\n"]
]
if (!check) await mkdir(directory, { recursive: true })
for (const [name, content] of outputs) {
  const destination = new URL(name, directory)
  if (check) {
    if (await readFile(destination, 'utf8') !== content) throw new Error(`生成文件已陈旧：${name}，请运行 npm run gen:api`)
  } else {
    await writeFile(destination, content)
  }
}
console.log(`${check ? '已核验' : '已生成'} HTTP 类型、事件联合类型与事件校验契约`)
