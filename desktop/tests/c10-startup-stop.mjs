import assert from 'node:assert/strict'
import { spawn, execFileSync } from 'node:child_process'
import { mkdirSync, existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'

// 使用真实 Electron、uv 和 Python；验收目录由 .gitignore 排除。
const directory = resolve('.artifacts', `c10-stop-${Date.now()}`)
mkdirSync(directory, { recursive: true })
const electron = spawn('node_modules/electron/dist/Electron.app/Contents/MacOS/Electron', ['.', `--user-data-dir=${directory}`], { stdio: 'ignore' })
const started = Date.now()
const log = `${directory}/data/logs/sidecar.log`
const processes = () => execFileSync('ps', ['-axo', 'pid,ppid,pgid,stat,command'], { encoding: 'utf8' }).split('\n')
const python = () => processes().find((line) => line.includes('/python3 -m agentcrew_server') && line.includes(directory))
let first
for (let attempt = 0; attempt < 500 && !first; attempt++) {
  first = python()
  if (!first) await sleep(10)
}
assert.ok(first, '真实 Python 未启动')
const pid = Number(first.trim().split(/\s+/)[0])
process.kill(pid, 'SIGSTOP')
const uv = Number(first.trim().split(/\s+/)[1])
process.kill(uv, 'SIGSTOP')
assert.ok(!existsSync(log) || !readFileSync(log, 'utf8').includes('http.ready'), '必须在就绪之前暂停')
assert.match(python(), /\sT\s/)
console.log(JSON.stringify({ event: 'stopped-before-ready', electron: electron.pid, uv, python: pid }))
let replacement
for (let attempt = 0; attempt < 480; attempt++) {
  const current = python()
  if (current && Number(current.trim().split(/\s+/)[0]) !== pid && existsSync(log) && readFileSync(log, 'utf8').includes('http.ready')) {
    replacement = current
    break
  }
  await sleep(100)
}
assert.ok(replacement, '启动超时后没有自动重启')
assert.ok(!processes().some((line) => Number(line.trim().split(/\s+/)[0]) === pid), '原 Python 仍然存在')
assert.match(readFileSync(`${directory}/sidecar-launch.log`, 'utf8'), /启动超过 30 秒/)
const elapsedMs = Date.now() - started
assert.ok(elapsedMs < 45_000, '清理与重启超出期限')
assert.ok(elapsedMs >= 35_000, '应当经过启动超时与 SIGKILL 升级期限')
console.log(JSON.stringify({ event: 'restarted', elapsedMs, process: replacement.trim() }))
const [replacementPid, replacementUv] = replacement.trim().split(/\s+/).map(Number)
process.kill(replacementPid, 'SIGSTOP')
process.kill(replacementUv, 'SIGSTOP')
const quitting = Date.now()
electron.kill('SIGTERM')
await new Promise((resolveExit) => electron.once('exit', resolveExit))
assert.equal(python(), undefined)
const quitElapsedMs = Date.now() - quitting
assert.ok(quitElapsedMs >= 5_000 && quitElapsedMs < 8_000, '退出清理必须有界升级 SIGKILL')
console.log(JSON.stringify({ event: 'exited', quitElapsedMs, orphanPython: false }))
