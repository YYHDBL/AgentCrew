import assert from 'node:assert/strict'

const phase = process.argv[2]
assert.ok(phase === 'before' || phase === 'after')
const targets = await (await fetch('http://127.0.0.1:9229/json/list')).json()
const target = targets.find((entry) => entry.type === 'page' && entry.title === 'AgentCrew')
assert.ok(target)

const expression = phase === 'before' ? `
  (async () => {
    const port = await window.agentcrew.getBackendPort()
    const token = await window.agentcrew.getToken()
    globalThis.__c10OldToken = token
    const request = (credential) => fetch('http://127.0.0.1:' + port + '/api/diagnostics', {
      headers: { Authorization: 'Bearer ' + credential }
    }).then((response) => response.status)
    return { port, correct: await request(token), wrong: await request('incorrect-token') }
  })()
` : `
  (async () => {
    const old = globalThis.__c10OldToken
    let port = null
    let token = null
    for (let attempt = 0; attempt < 100; attempt++) {
      port = await window.agentcrew.getBackendPort()
      token = await window.agentcrew.getToken()
      if (port && token && token !== old) break
      await new Promise((resolve) => setTimeout(resolve, 100))
    }
    const request = (credential) => fetch('http://127.0.0.1:' + port + '/api/diagnostics', {
      headers: { Authorization: 'Bearer ' + credential }
    }).then((response) => response.status)
    const result = { port, newCredential: await request(token), oldCredential: await request(old), rotated: token !== old }
    delete globalThis.__c10OldToken
    return result
  })()
`

const socket = new WebSocket(target.webSocketDebuggerUrl)
const result = await new Promise((resolve, reject) => {
  socket.addEventListener('open', () => socket.send(JSON.stringify({
    id: 1, method: 'Runtime.evaluate', params: { expression, awaitPromise: true, returnByValue: true }
  })))
  socket.addEventListener('error', reject)
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data)
    if (message.id !== 1) return
    if (message.result.exceptionDetails) reject(new Error(message.result.exceptionDetails.text))
    else resolve(message.result.result.value)
    socket.close()
  })
})

if (phase === 'before') {
  assert.equal(result.correct, 200)
  assert.equal(result.wrong, 401)
} else {
  assert.equal(result.newCredential, 200)
  assert.equal(result.oldCredential, 401)
  assert.equal(result.rotated, true)
}
console.log(JSON.stringify({ phase, ...result }))
