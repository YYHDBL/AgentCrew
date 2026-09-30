import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { createServer } from 'node:net'
import { join, resolve } from 'node:path'
import { app, dialog } from 'electron'

const READY = /^AGENTCREW_READY (\{"port":\s*\d+\})$/
const RESTART_WINDOW_MS = 60_000
const MAX_RESTARTS = 3

export class Sidecar {
  private child: ChildProcessWithoutNullStreams | null = null
  private port: number | null = null
  private token: string | null = null
  private stopping = false
  private restarts: number[] = []
  private timer: NodeJS.Timeout | null = null
  readonly dataDir = join(app.getPath('userData'), 'data')
  readonly logPath = join(this.dataDir, 'logs', 'sidecar.log')

  getBackendPort(): number | null { return this.port }
  getToken(): string | null { return this.port === null ? null : this.token }

  async start(): Promise<void> {
    if (this.stopping) return
    const requestedPort = await new Promise<number>((resolvePort, reject) => {
      const server = createServer()
      server.once('error', reject)
      server.listen(0, '127.0.0.1', () => {
        const address = server.address()
        if (!address || typeof address === 'string') { server.close(); reject(new Error('无法分配本地端口')); return }
        server.close(() => resolvePort(address.port))
      })
    }).catch((error: Error) => {
      this.failed(`无法分配本地端口：${error.message}`)
      return null
    })
    if (requestedPort === null) return
    const token = randomBytes(32).toString('hex')
    const child = spawn('uv', ['run', 'python', '-m', 'agentcrew_server', '--port', String(requestedPort), '--data-dir', this.dataDir, '--parent-pid', String(process.pid)], {
      cwd: resolve(app.getAppPath(), '../backend'),
      env: { ...process.env, AGENTCREW_TOKEN: token },
      stdio: ['pipe', 'pipe', 'pipe']
    })
    child.stdin.end()
    this.child = child
    this.port = null
    this.token = null
    let stdout = ''
    let settled = false
    let failureReason: string | null = null
    let actualPort: number | null = null
    const fail = (reason: string): void => {
      if (settled) return
      settled = true
      clearTimeout(timeout)
      clearInterval(health)
      failureReason = reason
      child.kill('SIGTERM')
    }
    child.stdout.on('data', (chunk: Buffer) => {
      stdout += chunk.toString('utf8')
      for (;;) {
        const newline = stdout.indexOf('\n')
        if (newline < 0) break
        const line = stdout.slice(0, newline).trim()
        stdout = stdout.slice(newline + 1)
        if (!line.startsWith('AGENTCREW_READY')) continue
        const match = READY.exec(line)
        if (!match || actualPort !== null) { fail('就绪标记格式异常'); return }
        const parsed = JSON.parse(match[1]) as { port?: unknown }
        if (typeof parsed.port !== 'number' || !Number.isInteger(parsed.port) || parsed.port < requestedPort || parsed.port > requestedPort + 3 || parsed.port > 65535) { fail('就绪端口无效'); return }
        actualPort = parsed.port
      }
      if (stdout.length > 8192) fail('就绪标记输出过长')
    })
    child.stderr.resume() // 后端自行轮转记录日志，保持管道畅通。
    child.once('error', (error) => fail(`启动进程失败：${error.message}`))
    child.once('close', (code, signal) => {
      if (this.child !== child) return
      this.child = null
      this.port = null
      this.token = null
      clearTimeout(timeout)
      clearInterval(health)
      if (!this.stopping) this.failed(failureReason ?? `进程退出：${code ?? signal}`)
    })
    const timeout = setTimeout(() => fail('启动超过 30 秒'), 30_000)
    const health = setInterval(async () => {
      if (settled || actualPort === null) return
      try {
        const response = await fetch(`http://127.0.0.1:${actualPort}/api/health`, { signal: AbortSignal.timeout(1000) })
        if (!response.ok || (await response.json() as { data?: { status?: string } }).data?.status !== 'ok') return
        if (settled || this.child !== child) return
        settled = true
        clearTimeout(timeout)
        clearInterval(health)
        this.port = actualPort
        this.token = token
      } catch { /* 启动期间继续轮询。 */ }
    }, 250)
  }

  private failed(reason: string): void {
    if (this.stopping || this.timer) return
    const now = Date.now()
    this.restarts = this.restarts.filter((time) => now - time < RESTART_WINDOW_MS)
    if (this.restarts.length >= MAX_RESTARTS) {
      void dialog.showMessageBox({ type: 'error', title: '任务服务无法启动', message: reason, detail: `已达到自动重启上限。日志：${this.logPath}` })
      return
    }
    this.restarts.push(now)
    this.timer = setTimeout(() => {
      this.timer = null
      void this.start()
    }, 2 ** (this.restarts.length - 1) * 1000)
  }

  stop(): void {
    this.stopping = true
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    this.child?.kill('SIGTERM')
  }
}
