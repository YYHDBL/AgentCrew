import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, stat, writeFile } from 'node:fs/promises'
import { DatabaseSync } from 'node:sqlite'

export async function evidence(directory) {
  const config = JSON.parse(await readFile(`${directory}/data/config.json`, 'utf8'))
  const secrets = new Set(Object.values(config.models).map((model) => model.api_key).filter(Boolean))
  const data = { started_at: new Date().toISOString(), http: [], sql: [], files: [], capture_errors: [] }
  const pending = new Set()
  const redact = (value) => {
    if (Array.isArray(value)) return value.map(redact)
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !/^(api_key|identity_token|access_token|authorization|token|thinking_blocks)$/i.test(key))
      .map(([key, entry]) => [key, redact(entry)]))
    if (typeof value === 'string') for (const secret of secrets) value = value.replaceAll(secret, '[已脱敏]')
    return value
  }
  return {
    attach(page) {
      page.on('response', (response) => {
        const url = new URL(response.url())
        if (url.hostname !== '127.0.0.1' || !url.pathname.startsWith('/api/') || !response.headers()['content-type']?.includes('application/json')) return
        const task = (async () => {
          const body = await response.json()
          if (body.data?.identity_token) secrets.add(body.data.identity_token)
          const request = response.request()
          const payload = request.postData()
          data.http.push(redact({ at: new Date().toISOString(), method: request.method(), path: url.pathname + url.search,
            request: payload ? JSON.parse(payload) : null, status: response.status(), response: body }))
        })().catch((error) => data.capture_errors.push(error.message))
        pending.add(task)
        void task.finally(() => pending.delete(task))
      })
    },
    async file(path) {
      const bytes = await readFile(path)
      const metadata = await stat(path)
      data.files.push({ path, sha256: createHash('sha256').update(bytes).digest('hex'), bytes: bytes.length, mtime: metadata.mtime.toISOString() })
    },
    async save(result, queries) {
      await Promise.all([...pending])
      const database = new DatabaseSync(`${directory}/data/agentcrew.db`, { readOnly: true })
      try {
        for (const sql of queries) data.sql.push({ sql, rows: redact(database.prepare(sql).all()) })
      } finally { database.close() }
      const text = JSON.stringify(redact({ ...result, evidence: data, finished_at: new Date().toISOString() }), null, 2)
      assert.ok([...secrets].every((secret) => !text.includes(secret)), '检查证据包含机密值')
      await writeFile(`${directory}/result.json`, text)
    }
  }
}
