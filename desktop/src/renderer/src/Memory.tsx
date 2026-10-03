import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Button, Modal } from 'antd'
import { api, ApiFailure, type Frame } from './session'

export type MemoryKind = 'user' | 'workspace' | 'soul' | 'skill'
export const memoryNames: Record<MemoryKind, string> = { user: 'USER 记忆', workspace: '工作区记忆', soul: '员工 soul', skill: '技能管理' }
const states: Record<string, string> = { active: '活动', stale: '陈旧', archived: '已归档', pinned: '已固定', queued: '排队中', running: '运行中', waiting_approval: '等待审批', completed: '已完成', cancelled: '已取消', interrupted: '已中断', failed: '失败', pending: '等待决定', allowed: '本次允许', rejected: '本次拒绝', expired: '已失效' }
interface Entry { entry_id: string; entry_hash: string; text: string; state: string; hits: number; last_hit_at: string | null; created_at: string; source: Record<string, unknown>; basis: string; needs_review: boolean; reviewed_by: string | null; reviewed_at: string | null }
interface Store { store_type: MemoryKind; store_id: string; revision: number; quota: number | null; used_characters: number; entries: Entry[]; next_after: string | null; at_global_seq: number; text: string; name?: string; description?: string; files?: string[] }
interface Skill { id: string; name: string; description: string; revision: number; state: string }
interface LedgerFile { path: string; content: string | null; sha256: string | null }
interface Ledger { id: number; change_id: string; action: string; before_text: string; after_text: string; before_metadata: Record<string, unknown>; after_metadata: Record<string, unknown>; before_files: LedgerFile[]; after_files: LedgerFile[]; created_at: string; source: Record<string, unknown> }
interface Approval { id: string; call_id: string; input_hash: string; tool: string; input: Record<string, unknown>; status: string }
interface Job { id: string; kind: string; status: string; task_run_id: string | null; model: string | null; error: string | null; report: Record<string, unknown> | null; approvals: Approval[]; usage: { input_tokens: number; output_tokens: number }; created_at: string }
interface MemorySettings { user_quota: number; workspace_quota: number; soul_quota: number; write_approval: boolean }
interface Page<T> { items: T[]; next_after: string | null }
interface Confirmation { title: string; label: string; detail: JSX.Element; run: () => Promise<void> }
const date = (value: string | null): string => value ? new Date(value).toLocaleString('zh-CN') : '尚未使用'
const jobName = (kind: string): string => ({ summary: '任务摘要', memory_review: '记忆提炼', skill_review: '技能审查', curate: '记忆治理' })[kind] ?? kind

function LedgerFiles({ files }: { files: LedgerFile[] }): JSX.Element {
  return <div>{files.map((file) => <details key={file.path}><summary>{file.path}</summary><p>{file.content === null ? '文件不存在' : `SHA ${file.sha256}`}</p><pre>{file.content}</pre></details>)}</div>
}

export function MemoryNotice({ events }: { events: Frame[] }): JSX.Element | null {
  const latest = events.filter((event) => ['memory.updated', 'memory.archived', 'skill.patched', 'memory.curated', 'context.compacted'].includes(event.type)).at(-1)
  if (!latest) return null
  const label = latest.type === 'skill.patched' ? '技能已修改' : latest.type === 'memory.archived' ? '记忆已归档' : latest.type === 'memory.curated' ? '记忆治理已完成' : latest.type === 'context.compacted' ? '上下文已压缩' : '记住了'
  return <div className="memory-notice" role="status"><strong>{label}</strong> {String(latest.payload.summary ?? '')}<small>{date(latest.ts)}</small></div>
}

export function SoulMemory(props: Omit<Parameters<typeof Memory>[0], 'kind'>): JSX.Element {
  return <Memory {...props} kind="soul" />
}

export function Memory({ kind, workspace, agent, events, connectionStatus }: { kind: MemoryKind; workspace: string; agent: string; events: Frame[]; connectionStatus: string }): JSX.Element {
  const scope = new URLSearchParams({ workspace_id: workspace, agent_id: agent }).toString()
  const selectionKey = `memory-selection:${scope}:${kind}`
  const [store, setStore] = useState<Store | null>(null)
  const [entries, setEntries] = useState<Entry[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [skillAfter, setSkillAfter] = useState<string | null>(null)
  const [skillId, setSkillId] = useState<string | null>(() => sessionStorage.getItem(`${selectionKey}:skill`))
  const [selectedId, setSelectedId] = useState<string | null>(() => sessionStorage.getItem(`${selectionKey}:entry`))
  const [filter, setFilter] = useState('all')
  const [draft, setDraft] = useState(false)
  const [creatingSkill, setCreatingSkill] = useState(false)
  const [text, setText] = useState('')
  const [basis, setBasis] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [fileName, setFileName] = useState('')
  const [fileText, setFileText] = useState('')
  const [fileLoaded, setFileLoaded] = useState(false)
  const [ledger, setLedger] = useState<Ledger[]>([])
  const [ledgerAfter, setLedgerAfter] = useState<string | null>(null)
  const [ledgerVisible, setLedgerVisible] = useState(false)
  const [jobs, setJobs] = useState<Job[]>([])
  const [jobsAfter, setJobsAfter] = useState<string | null>(null)
  const [settings, setSettings] = useState<MemorySettings | null>(null)
  const [settingsDraft, setSettingsDraft] = useState<MemorySettings | null>(null)
  const [settingsVisible, setSettingsVisible] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [readonly, setReadonly] = useState(false)
  const [confirm, setConfirm] = useState<Confirmation | null>(null)
  const [search, setSearch] = useState('')
  const [searchAfter, setSearchAfter] = useState<string | null>(null)
  const [searchArchived, setSearchArchived] = useState(false)
  const [hits, setHits] = useState<{ id: string; text: string; kind: string; source: Record<string, unknown> }[]>([])
  const newButton = useRef<HTMLButtonElement>(null)
  const textInput = useRef<HTMLTextAreaElement>(null)
  const draftRevision = useRef(0)
  const draftTarget = useRef<Entry | null>(null)
  const draftStorePath = useRef<string | null>(null)
  const mutationIds = useRef(new Map<string, string>())
  const selected = entries.find((entry) => entry.entry_id === selectedId) ?? null
  const storeId = kind === 'user' ? 'owner' : kind === 'workspace' ? workspace : kind === 'soul' ? agent : skillId
  const path = storeId ? `/memory/stores/${kind}/${storeId}` : null
  const selectionIdentity = `${scope}:${kind}:${skillId ?? ''}:${filter}`
  const selection = useRef(selectionIdentity)
  useLayoutEffect(() => {
    if (selection.current !== selectionIdentity) { setStore(null); setEntries([]); setSelectedId(null); setLedger([]) }
    selection.current = selectionIdentity; setFileName(''); setFileText(''); setFileLoaded(false)
  }, [selectionIdentity])
  const reportError = (reason: unknown): void => {
    const failure = reason instanceof Error ? reason.message : String(reason)
    setError(failure)
    if (reason instanceof ApiFailure && ['DIAGNOSTIC_MODE', 'OUT_OF_SCOPE', 'STORE_RECOVERING', 'EXTERNAL_MODIFICATION'].includes(reason.code)) setReadonly(true)
  }
  useEffect(() => {
    if (selectedId) sessionStorage.setItem(`${selectionKey}:entry`, selectedId)
    else sessionStorage.removeItem(`${selectionKey}:entry`)
    if (skillId) sessionStorage.setItem(`${selectionKey}:skill`, skillId)
    else sessionStorage.removeItem(`${selectionKey}:skill`)
  }, [selectionKey, selectedId, skillId])
  const load = async (signal?: AbortSignal): Promise<void> => {
    const [jobPage, configuration] = await Promise.all([
      api<Page<Job>>(`/memory/jobs?${scope}`, undefined, signal), api<{ memory: MemorySettings }>('/settings', undefined, signal)
    ])
    if (signal?.aborted || selection.current !== selectionIdentity) return
    setJobs(jobPage.items); setJobsAfter(jobPage.next_after); setSettings(configuration.memory)
    if (kind === 'skill') {
      const index = await api<Page<Skill>>(`/memory/skills?${scope}&archived=true`, undefined, signal)
      if (signal?.aborted || selection.current !== selectionIdentity) return
      setSkills(index.items); setSkillAfter(index.next_after)
    }
    if (path) {
      const value = await api<Store>(`${path}?${scope}&state=${filter}`, undefined, signal)
      if (signal?.aborted || selection.current !== selectionIdentity) return
      setStore(value); setEntries(value.entries)
      if (kind === 'skill') setSelectedId((previous) => previous ?? value.entries[0]?.entry_id ?? null)
      if (ledgerVisible) {
        const history = await api<Page<Ledger>>(`/memory/ledger?${scope}&store_type=${kind}&store_id=${storeId}`, undefined, signal)
        if (!signal?.aborted && selection.current === selectionIdentity) { setLedger(history.items); setLedgerAfter(history.next_after) }
      }
    }
    if (!signal?.aborted && selection.current === selectionIdentity) setReadonly(false)
  }
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(''); setReadonly(false)
    void load(controller.signal).catch((reason) => { if (!controller.signal.aborted) reportError(reason) }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [kind, workspace, agent, skillId, filter, ledgerVisible])
  const watermark = events.at(-1)?.global_seq ?? 0
  useEffect(() => {
    const controller = new AbortController()
    const timer = setTimeout(() => { void load(controller.signal).catch((reason) => { if (!controller.signal.aborted) reportError(reason) }) }, 150)
    return () => { clearTimeout(timer); controller.abort() }
  }, [watermark, selectionIdentity, ledgerVisible])
  useLayoutEffect(() => {
    if (draft) textInput.current?.focus()
  }, [draft])
  const operate = async (run: () => Promise<void>, message: string, refresh = true): Promise<void> => {
    setBusy(true); setError(''); setNote('')
    try { await run(); setNote(message); if (refresh) await load(); setConfirm(null) }
    catch (reason) { reportError(reason); if (reason instanceof ApiFailure && reason.code === 'REVISION_CONFLICT') await load() }
    finally { setBusy(false) }
  }
  const mutation = (revision: number, extra: Record<string, unknown> = {}, target = path): Record<string, unknown> => {
    const body = { expected_revision: revision, basis: '所有者在记忆界面明确操作', ...extra }
    const key = JSON.stringify({ target, scope, body })
    if (!mutationIds.current.has(key)) mutationIds.current.set(key, crypto.randomUUID())
    return { change_id: mutationIds.current.get(key), ...body }
  }
  const select = (entry: Entry): void => { setSelectedId(entry.entry_id); setDraft(false); setFileLoaded(false); setNote(''); setError('') }
  const begin = (entry?: Entry): void => {
    setSelectedId(entry?.entry_id ?? null); setCreatingSkill(kind === 'skill' && !entry); setText(entry?.text ?? '')
    draftTarget.current = entry ? { ...entry } : null
    draftStorePath.current = path
    setBasis(''); setName(kind === 'skill' && !entry ? '' : store?.name ?? ''); setDescription(kind === 'skill' && !entry ? '' : store?.description ?? '')
    draftRevision.current = store?.revision ?? 0
    setDraft(true); setError(''); setNote(''); setLedgerVisible(false)
  }
  const save = async (): Promise<void> => {
    await operate(async () => {
      let value: { entry_id: string | null; store_id?: string }
      if (creatingSkill) {
        value = await api('/memory/skills', mutation(0, { workspace_id: workspace, agent_id: agent, name, description, text, basis }, '/memory/skills'))
        setSkillId(value.store_id!)
      } else {
        if (!path || draftStorePath.current !== path) throw new Error('编辑目标已发生变化，请取消编辑并重新读取目标记忆库。')
        const editing = draftTarget.current
        const target = editing ? `${path}/entries/${editing.entry_hash}` : path
        value = await api(`${target}?${scope}`, mutation(draftRevision.current, { text, basis,
          ...(kind === 'skill' ? { description } : {}) }, `${editing ? 'PATCH' : 'POST'} ${target}`), undefined, editing ? 'PATCH' : 'POST')
      }
      setSelectedId(value.entry_id); setDraft(false)
    }, kind === 'skill' ? '技能已保存' : '记忆已保存')
  }
  const entryAction = (action: string, label: string): void => {
    if (!selected || !store || !path) return
    const entry = selected
    const endpoint = `${path}/entries/${entry.entry_hash}/${action === 'approve' || action === 'reject' ? 'review' : action}?${scope}`
    setConfirm({ title: label, label: `确认${label}`, detail: <><p>操作对象：{entry.text}</p><p>{action === 'approve' ? '审核通过后，新会话注入及检索可使用该事实。' : action === 'archive' ? '归档保留完整内容和历史记录，可通过恢复条目重新启用。' : action === 'restore' ? '恢复后该条目重新占用活动库配额。' : '此操作将追加变更记录。'}</p></>,
      run: async () => { await api(endpoint, mutation(store.revision, action === 'approve' || action === 'reject' ? { review_decision: action } : {}, endpoint)) } })
  }
  const moreEntries = async (): Promise<void> => {
    if (!store?.next_after || !path) return
    const next = await api<Store>(`${path}?${scope}&state=${filter}&after=${encodeURIComponent(store.next_after)}`)
    setEntries((previous) => [...previous, ...next.entries]); setStore(next)
  }
  const readFile = async (file: string): Promise<void> => {
    setFileName(file); setFileLoaded(false)
    await operate(async () => {
      const value = await api<{ text: string; revision: number }>(`/memory/skills/${skillId}/file?${scope}&file=${encodeURIComponent(file)}`)
      if (selection.current !== selectionIdentity) return
      setFileText(value.text); setFileLoaded(true); draftRevision.current = value.revision
    }, '已读取技能支撑文件')
  }
  const saveFile = async (remove = false): Promise<void> => {
    if (!selected || !path || !store) return
    const target = `${path}/entries/${selected.entry_hash}`
    await api(`${target}?${scope}`, mutation(fileLoaded ? draftRevision.current : store.revision,
      { text: selected.text, files: { [fileName]: remove ? null : fileText } }, `PATCH ${target}`), undefined, 'PATCH')
    setFileLoaded(false)
  }
  const confirmCuration = (): void => {
    const client_request_id = crypto.randomUUID()
    setConfirm({ title: '治理当前记忆', label: '确认治理', detail: <p>检查十四天及三十天使用记录，保护固定条目和有效引用，完整归档闲置材料。</p>, run: async () => { await api('/memory/curate/run', { workspace_id: workspace, agent_id: agent, client_request_id }) } })
  }
  const filteredEvents = events.filter((event) => ['memory.updated', 'memory.archived', 'skill.patched', 'memory.curated', 'context.compacted'].includes(event.type)).slice(-12).reverse()
  return <main className="memory-page" aria-label={memoryNames[kind]} aria-busy={loading || busy}>
    <div className="memory-heading"><div><h1>{memoryNames[kind]}</h1><p>{kind === 'soul' ? `当前员工：${agent}` : kind === 'user' ? '所有者的长期偏好与习惯' : `工作区：${workspace}　当前员工：${agent}`}</p></div><span role="status">{connectionStatus}</span></div>
    <MemoryNotice events={events} />
    {error && <p id="memory-error" role="alert">{error} {readonly ? '当前内容为只读状态。' : '输入草稿已保留，可重新读取当前内容并核查修订。'}</p>}
    {note && <p className="memory-success" role="status">{note}</p>}
    <div className="memory-toolbar"><Button ref={newButton} disabled={loading || busy || readonly || (kind === 'skill' && draft)} onClick={() => begin()}>{kind === 'skill' ? '新建技能' : '新建条目'}</Button>
      <Button disabled={busy} onClick={() => void operate(async () => { await load() }, '当前内容已刷新')}>刷新记忆</Button>
      <Button disabled={!settings} onClick={() => { setSettingsDraft(settings); setSettingsVisible(true) }}>记忆设置</Button>
      <Button disabled={busy || readonly} onClick={confirmCuration}>治理记忆</Button>
      <label>条目状态 <select aria-label="条目状态" disabled={busy || draft} value={filter} onChange={(event) => { setFilter(event.target.value); setSelectedId(null) }}><option value="all">全部状态</option>{['active', 'stale', 'archived', 'pinned'].map((state) => <option key={state} value={state}>{states[state]}</option>)}</select></label>
    </div>
    {loading && <p role="status">正在读取实际记忆资料。</p>}
    <div className="memory-body"><aside className="memory-index" aria-label={kind === 'skill' ? '技能索引' : '记忆条目'}>
      {kind === 'skill' ? <>{skills.map((skill) => <button type="button" disabled={busy || draft} key={skill.id} aria-current={skillId === skill.id ? 'true' : undefined} onClick={() => { setSkillId(skill.id); setSelectedId(null); setDraft(false); setLedgerVisible(false) }}><strong>{skill.name}</strong><span>{skill.description}</span><small>{states[skill.state]}　修订 {skill.revision}</small></button>)}{skillAfter && <Button onClick={() => void operate(async () => { const next = await api<Page<Skill>>(`/memory/skills?${scope}&archived=true&after=${encodeURIComponent(skillAfter)}`); setSkills((previous) => [...previous, ...next.items]); setSkillAfter(next.next_after) }, '', false)}>更多技能</Button>}</>
        : entries.map((entry) => <button type="button" disabled={busy || draft} key={entry.entry_id} aria-current={selectedId === entry.entry_id ? 'true' : undefined} onClick={() => select(entry)}><span>{entry.text.split('\n')[0]}</span><small>{states[entry.state]}{entry.needs_review ? '　待人工审核' : ''}</small></button>)}
      {!loading && (kind === 'skill' ? skills.length === 0 : entries.length === 0) && <p>当前范围没有条目，可以新建记忆。</p>}
      {kind !== 'skill' && store?.next_after && <Button onClick={() => void operate(moreEntries, '', false)}>更多条目</Button>}
    </aside><section className="memory-detail" aria-label="记忆内容">
      {store && <div className="memory-quota"><span>修订 {store.revision}</span><span>{store.quota === null ? '技能正文按需读取' : `${store.used_characters} / ${store.quota} 字符`}</span>{store.quota !== null && <progress value={store.used_characters} max={store.quota} aria-label="记忆配额占用" />}</div>}
      {draft ? <form onSubmit={(event) => { event.preventDefault(); void save() }} aria-describedby={error ? 'memory-error' : undefined}>
        {kind === 'skill' && <><label>技能名称<input aria-label="技能名称" required disabled={!creatingSkill || busy} maxLength={80} value={name} onChange={(event) => setName(event.target.value)} /></label><label>技能描述<input aria-label="技能描述" required maxLength={60} value={description} onChange={(event) => setDescription(event.target.value)} /><small>{description.length} / 60 字符</small></label></>}
        <label>{kind === 'skill' ? '技能正文' : '记忆正文'}<textarea ref={textInput} aria-label={kind === 'skill' ? '技能正文' : '记忆正文'} required value={text} onChange={(event) => setText(event.target.value)} disabled={busy} /></label>
        <label>保存依据<textarea aria-label="保存依据" required value={basis} onChange={(event) => setBasis(event.target.value)} disabled={busy} /></label>
        {error && store && store.revision !== draftRevision.current && <Button onClick={() => setConfirm({ title: '核查当前修订', label: '采用当前修订', detail: <><p>当前正文：</p><pre>{store.text}</pre><p>保留的输入草稿：</p><pre>{text}</pre></>, run: async () => { if (draftTarget.current) { const current = entries.find((entry) => entry.entry_id === draftTarget.current!.entry_id); if (!current) throw new Error('原条目已不在当前列表中，请取消编辑后读取该条目。'); draftTarget.current = { ...current } } draftRevision.current = store.revision; setError('') } })}>核查当前修订</Button>}
        <div className="card-actions"><Button htmlType="submit" type="primary" disabled={busy || readonly || !text.trim() || !basis.trim() || (kind === 'skill' && (!name.trim() || !description.trim()))}>{kind === 'skill' ? '保存技能' : '保存记忆'}</Button><Button disabled={busy} onClick={() => { setDraft(false); newButton.current?.focus() }}>取消编辑</Button></div>
      </form> : <>{(selected || kind === 'skill' && store?.entries[0]) ? (() => {
        const entry = selected ?? store!.entries[0]
        if (!selected && kind === 'skill') return <><h2>{store!.name}</h2><pre className="memory-text">{entry.text}</pre><Button disabled={loading || busy} onClick={() => select(entry)}>选择技能正文</Button></>
        return <><h2>{kind === 'skill' ? store?.name : '条目内容'}</h2><pre className="memory-text">{entry.text}</pre>
          <dl><dt>状态</dt><dd>{states[entry.state]}</dd><dt>使用次数</dt><dd>{entry.hits}</dd><dt>最后使用</dt><dd>{date(entry.last_hit_at)}</dd><dt>来源</dt><dd><pre>{JSON.stringify(entry.source, null, 2)}</pre></dd><dt>依据</dt><dd>{entry.basis}</dd><dt>人工审核</dt><dd>{entry.needs_review ? '待人工审核' : entry.reviewed_by ? '已通过人工审核' : '无需人工审核'}</dd></dl>
          <div className="card-actions"><Button disabled={loading || busy || readonly || entry.state === 'archived'} onClick={() => begin(entry)}>编辑条目</Button>
            {entry.state === 'archived' ? <Button disabled={busy || readonly} onClick={() => entryAction('restore', '恢复')}>恢复条目</Button> : <><Button disabled={busy || readonly} onClick={() => void operate(async () => { const target = `${path}/entries/${entry.entry_hash}/${entry.state === 'pinned' ? 'unpin' : 'pin'}`; await api(`${target}?${scope}`, mutation(store!.revision, {}, target)) }, entry.state === 'pinned' ? '已取消固定' : '条目已固定')}>{entry.state === 'pinned' ? '取消固定' : '固定条目'}</Button><Button danger disabled={busy || readonly} onClick={() => entryAction('archive', '归档')}>归档条目</Button></>}
            {entry.needs_review && <><Button disabled={busy || readonly} onClick={() => entryAction('approve', '审核通过')}>审核通过</Button><Button disabled={busy || readonly} onClick={() => entryAction('reject', '拒绝审核')}>拒绝审核</Button></>}
            <Button onClick={() => setLedgerVisible(!ledgerVisible)} aria-expanded={ledgerVisible}>查看变更记录</Button>
          </div></>
      })() : <p>选择条目查看完整正文、来源和依据。</p>}</>}
      {kind === 'skill' && store && !draft && <section className="memory-files"><h2>支撑文件</h2>{store.files?.map((file) => <Button disabled={busy} key={file} onClick={() => void readFile(file)}>{file}</Button>)}
        <label>支撑文件路径<input aria-label="支撑文件路径" disabled={busy} value={fileName} placeholder="references/checklist.md" onChange={(event) => { setFileName(event.target.value); setFileLoaded(false) }} /></label><label>支撑文件正文<textarea aria-label="支撑文件正文" disabled={busy} value={fileText} onChange={(event) => setFileText(event.target.value)} /></label>
        <div className="card-actions"><Button disabled={busy || readonly || !selected || !fileName.trim()} onClick={() => void operate(() => saveFile(), '支撑文件已保存')}>保存支撑文件</Button><Button danger disabled={busy || readonly || !selected || !fileLoaded} onClick={() => setConfirm({ title: '删除支撑文件', label: '确认删除支撑文件', detail: <p>删除 {fileName}，完整历史仍保存在变更记录中。</p>, run: () => saveFile(true) })}>删除支撑文件</Button></div>
      </section>}
      {ledgerVisible && <section className="memory-ledger"><h2>变更记录</h2>{ledger.map((row) => <article key={row.id}><h3>记录 {row.id}　{row.action}</h3><p>{date(row.created_at)}</p><details><summary>完整变更内容</summary><p>变更前</p><pre>{row.before_text || '空白内容'}</pre><p>变更后</p><pre>{row.after_text || '空白内容'}</pre><pre>{JSON.stringify({ before: row.before_metadata, after: row.after_metadata, source: row.source }, null, 2)}</pre><h4>变更前文件</h4><LedgerFiles files={row.before_files} /><h4>变更后文件</h4><LedgerFiles files={row.after_files} /></details><Button disabled={busy || readonly} onClick={() => setConfirm({ title: '恢复此次变更前内容', label: '确认恢复变更', detail: <><p>恢复完整正文、metadata 和支撑文件，将追加新的变更记录。</p><pre>{row.before_text || '将恢复为空白内容。'}</pre><LedgerFiles files={row.before_files} /></>, run: async () => { const target = `/memory/ledger/${row.id}/rollback`; await api(`${target}?${scope}`, mutation(store!.revision, {}, target)); setDraft(false) } })}>恢复此次变更前内容</Button></article>)}{ledgerAfter && <Button onClick={() => void operate(async () => { const next = await api<Page<Ledger>>(`/memory/ledger?${scope}&store_type=${kind}&store_id=${storeId}&after=${encodeURIComponent(ledgerAfter)}`); setLedger((previous) => [...previous, ...next.items]); setLedgerAfter(next.next_after) }, '', false)}>更多变更记录</Button>}</section>}
    </section></div>
    <section className="memory-search"><h2>历史与记忆检索</h2><form onSubmit={(event) => { event.preventDefault(); void operate(async () => { const result = await api<Page<typeof hits[number]>>(`/memory/search?${scope}&archived=${searchArchived}&query=${encodeURIComponent(search)}`); setHits(result.items); setSearchAfter(result.next_after) }, '检索完成', false) }}><label>查询内容<input aria-label="查询内容" value={search} onChange={(event) => { setSearch(event.target.value); setSearchAfter(null) }} required maxLength={200} /></label><label><input type="checkbox" checked={searchArchived} onChange={(event) => { setSearchArchived(event.target.checked); setSearchAfter(null) }} />包含归档记忆</label><Button htmlType="submit" disabled={busy || !search.trim()}>搜索历史</Button></form>{hits.map((hit) => <details key={`${hit.kind}:${hit.id}`}><summary>{hit.text.slice(0, 100)}</summary><pre>{hit.text}</pre><pre>{JSON.stringify(hit.source, null, 2)}</pre></details>)}{searchAfter && <Button onClick={() => void operate(async () => { const next = await api<Page<typeof hits[number]>>(`/memory/search?${scope}&archived=${searchArchived}&query=${encodeURIComponent(search)}&after=${encodeURIComponent(searchAfter)}`); setHits((previous) => [...previous, ...next.items]); setSearchAfter(next.next_after) }, '', false)}>更多检索结果</Button>}</section>
    <section className="memory-jobs"><h2>后台作业</h2><p>前台任务完成后，后台作业继续记录提炼结果。新的任务会停止正在执行的辅助作业。</p>{jobs.map((job) => <article key={job.id} className="memory-job"><h3>{jobName(job.kind)}　{states[job.status]}</h3><p>作业 {job.id}　{date(job.created_at)}</p><p>{job.model ? `${job.model}　输入 ${job.usage.input_tokens}　输出 ${job.usage.output_tokens}` : '确定性治理，无模型调用'}</p>{job.error && <p role="status">{job.error}</p>}{job.report && <details><summary>实际作业结果</summary><pre>{JSON.stringify(job.report, null, 2)}</pre></details>}
      {job.approvals.map((approval) => <section key={approval.id} className="memory-approval"><h4>后台审批：{approval.tool}　{states[approval.status]}</h4><pre>{JSON.stringify(approval.input, null, 2)}</pre>{approval.status === 'pending' && job.status === 'waiting_approval' && <div className="card-actions">{[['allow_once', '本次允许'], ['reject_once', '本次拒绝']].map(([decision, label]) => <Button key={decision} disabled={busy || readonly} onClick={() => void operate(async () => { await api(`/memory/jobs/${job.id}/approvals/${approval.id}?${scope}`, { decision, input_hash: approval.input_hash }) }, `后台审批已${label}`)}>{label}</Button>)}</div>}</section>)}
      {['queued', 'running', 'waiting_approval'].includes(job.status) && <Button disabled={busy || readonly} onClick={() => void operate(async () => { await api(`/memory/jobs/${job.id}/cancel?${scope}`, {}) }, '后台作业已取消')}>取消后台作业</Button>}
    </article>)}{jobsAfter && <Button onClick={() => void operate(async () => { const next = await api<Page<Job>>(`/memory/jobs?${scope}&after=${encodeURIComponent(jobsAfter)}`); setJobs((previous) => [...previous, ...next.items]); setJobsAfter(next.next_after) }, '', false)}>更多后台作业</Button>}</section>
    <section className="memory-events"><h2>记忆事件</h2>{filteredEvents.map((event) => <p key={event.global_seq}>事件 {event.global_seq}　{event.type}　{String(event.payload.summary ?? '')}　{date(event.ts)}</p>)}</section>
    <Modal title={confirm?.title} open={confirm !== null} okText={confirm?.label} cancelText="取消操作" confirmLoading={busy} onCancel={() => { if (!busy) setConfirm(null) }} onOk={() => { if (confirm) void operate(confirm.run, `${confirm.title}已完成`) }}><div className="memory-confirm">{confirm?.detail}</div>{error && <p role="alert">{error}</p>}</Modal>
    <Modal title="记忆设置" open={settingsVisible} okText="保存记忆设置" cancelText="取消设置" confirmLoading={busy} onCancel={() => setSettingsVisible(false)} onOk={() => void operate(async () => { await api('/settings', { memory: settingsDraft }, undefined, 'PATCH'); setSettingsVisible(false) }, '记忆设置已保存')}>
      {settingsDraft && <><p>字符配额包含分隔符和换行，已归档条目不占活动库配额。</p>{[['user_quota', 'USER 配额'], ['workspace_quota', '工作区配额'], ['soul_quota', 'soul 配额']].map(([key, label]) => <label className="memory-setting" key={key}>{label}<input aria-label={label} type="number" min={1} value={settingsDraft[key as 'user_quota']} onChange={(event) => setSettingsDraft({ ...settingsDraft, [key]: Number(event.target.value) })} /></label>)}<label className="memory-setting"><input type="checkbox" checked={settingsDraft.write_approval} onChange={(event) => setSettingsDraft({ ...settingsDraft, write_approval: event.target.checked })} />后台写入需要人工审批</label></>}{error && <p role="alert">{error}</p>}
    </Modal>
  </main>
}
