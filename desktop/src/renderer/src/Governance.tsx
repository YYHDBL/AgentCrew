import { cloneElement, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { Button, Modal, Tabs } from 'antd'
import { api } from './session'
import { allPages, type Identity, type Resource, type AgentSpec, type Grant, type Rule, type Membership, type Version, type AuditRow, type AuditVerification, type Backup, type Diagnostic, type Page } from './governance-data'

const stateName = (status: string): string => ({ active: '有效', disabled: '已禁用', archived: '已归档' })[status] ?? status
const stamp = (value: string): string => new Date(value).toLocaleString('zh-CN')
const defaults: AgentSpec = { position: '', model_slot: 'main', skill_ids: [], connector_ids: [] }
type Editor = { kind: 'workspace' | 'agent' | 'skill' | 'connector'; resource: Resource | null; name: string; description: string; position: string; slot: 'main' | 'aux'; skillIds: string[]; connectorIds: string[]; text: string; config: string; credential: string; clearCredential: boolean; connectorType: 'http' | 'mcp' }
type Confirmation = { title: string; description: string; execute: () => Promise<void> }

function Field({ label, children }: { label: string; children: JSX.Element }): JSX.Element {
  const id = useId()
  return <div className="governance-field"><label htmlFor={id}>{label}</label>{cloneElement(children, { id })}</div>
}

export function Governance({ identity, workspace, agent, revision, diagnostic, changed, onDiagnostic, selectScope }: {
  identity: Identity; workspace: string; agent: string; revision: number; diagnostic: Diagnostic; changed: () => void; onDiagnostic: (value: Diagnostic) => void; selectScope: (workspace: string, agent: string) => void
}): JSX.Element {
  const [resources, setResources] = useState<{ workspaces: Resource[]; agents: Resource[]; skills: Resource[]; connectors: Resource[] }>({ workspaces: [], agents: [], skills: [], connectors: [] })
  const [grants, setGrants] = useState<Grant[]>([])
  const [rules, setRules] = useState<Rule[]>([])
  const [members, setMembers] = useState<Membership[]>([])
  const [organization, setOrganization] = useState<Resource | null>(null)
  const [organizationName, setOrganizationName] = useState('')
  const [editor, setEditor] = useState<Editor | null>(null)
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [versionSkill, setVersionSkill] = useState<Resource | null>(null)
  const [versions, setVersions] = useState<Version[]>([])
  const [selectedVersion, setSelectedVersion] = useState<Version | null>(null)
  const [versionText, setVersionText] = useState('')
  const [versionBasis, setVersionBasis] = useState('')
  const [grantKind, setGrantKind] = useState('skill')
  const [grantResource, setGrantResource] = useState('')
  const [grantee, setGrantee] = useState('')
  const [ruleTool, setRuleTool] = useState('write_file')
  const [rulePattern, setRulePattern] = useState('')
  const [ruleEffect, setRuleEffect] = useState('allow')
  const [actorFilter, setActorFilter] = useState('')
  const [actionFilter, setActionFilter] = useState('')
  const [resourceFilter, setResourceFilter] = useState('')
  const [audit, setAudit] = useState<AuditRow[]>([])
  const [auditAfter, setAuditAfter] = useState<string | null>(null)
  const [verification, setVerification] = useState<AuditVerification | null>(null)
  const [backups, setBackups] = useState<Backup[]>([])
  const [backupKind, setBackupKind] = useState('directory')
  const [restorePending, setRestorePending] = useState(false)
  const [tab, setTab] = useState(() => diagnostic.mode === 'diagnostic' ? 'audit' : 'employees')
  const mutationIds = useRef(new Map<string, string>())
  const viewGeneration = useRef(0)
  useLayoutEffect(() => {
    viewGeneration.current += 1
    setResources({ workspaces: [], agents: [], skills: [], connectors: [] }); setGrants([]); setRules([]); setMembers([])
    setVersionSkill(null); setVersions([]); setSelectedVersion(null); setVersionText(''); setVersionBasis(''); setAudit([]); setAuditAfter(null); setVerification(null)
    setEditor(null); setConfirmation(null); setError(''); setNote('')
  }, [workspace, agent, identity.role, identity.organization_status, identity.workspace_ids.join('|')])
  const canManage = identity.role !== 'member' && identity.organization_status === 'active' && diagnostic.mode === 'normal'
  const canOwn = identity.role === 'owner'
  const blockReason = diagnostic.mode === 'diagnostic' ? '只读诊断期间，业务操作已禁用。' : identity.organization_status !== 'active' ? '组织已经禁用。业务操作停止，owner可以在组织管理中重新启用。' : identity.role === 'member' ? '当前member角色仅能查看已授权资源，管理操作由owner或admin执行。' : ''
  const identifier = (operation: string, body: unknown): string => {
    const key = JSON.stringify({ operation, body })
    if (!mutationIds.current.has(key)) mutationIds.current.set(key, crypto.randomUUID())
    return mutationIds.current.get(key)!
  }
  const mutate = async (path: string, body: Record<string, unknown>, method = 'POST'): Promise<unknown> => api(path, { ...body, change_id: identifier(path + method, body) }, undefined, method)

  const load = async (signal?: AbortSignal): Promise<void> => {
    setLoading(true)
    try {
      if (diagnostic.mode === 'diagnostic') {
        if (canOwn) setBackups(await api<Backup[]>('/diagnostics/backups', undefined, signal))
        return
      }
      if (identity.organization_status !== 'active') {
        setOrganization(await api<Resource>('/organization', undefined, signal))
        setResources({ workspaces: [], agents: [], skills: [], connectors: [] }); setGrants([]); setRules([]); setMembers([])
        return
      }
      const [workspaces, agents, skills, grantRows, membershipRows, org] = await Promise.all([
        allPages<Resource>('/workspaces', signal), allPages<Resource>('/agents', signal), allPages<Resource>('/skills', signal),
        workspace ? allPages<Grant>(`/grants?workspace_id=${encodeURIComponent(workspace)}&revoked=true`, signal) : Promise.resolve([]), allPages<Membership>('/memberships', signal), api<Resource>('/organization', undefined, signal)
      ])
      let connectors: Resource[] = []
      if (identity.role !== 'member' && workspace) connectors = await allPages<Resource>(`/connectors?workspace_id=${encodeURIComponent(workspace)}`, signal)
      const ruleRows = agent ? await allPages<Rule>(`/agents/${agent}/permission-rules?revoked=true`, signal) : []
      if (signal?.aborted) return
      setResources({ workspaces, agents, skills, connectors }); setGrants(grantRows); setMembers(membershipRows); setOrganization(org); setRules(ruleRows)
      if (versionSkill && !skills.some((item) => item.id === versionSkill.id)) { viewGeneration.current += 1; setVersionSkill(null); setVersions([]); setSelectedVersion(null); setVersionText(''); setVersionBasis('') }
      setOrganizationName((value) => value || org.name)
      if (canOwn) setBackups(await api<Backup[]>('/diagnostics/backups', undefined, signal))
    } finally { if (!signal?.aborted) setLoading(false) }
  }
  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal).catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [identity.effective_user_id, identity.organization_status, workspace, agent, revision, diagnostic.mode])

  const operate = async (operation: () => Promise<void>, message: string): Promise<void> => {
    setBusy(true); setError(''); setNote('')
    try { await operation(); setNote(message); setConfirmation(null); changed(); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }
  const edit = (kind: Editor['kind'], resource: Resource | null): void => {
    const spec = resource?.spec ?? defaults
    setError('')
    setEditor({ kind, resource, name: resource?.name ?? '', description: resource?.description ?? '', position: spec.position, slot: spec.model_slot,
      skillIds: [...spec.skill_ids], connectorIds: [...spec.connector_ids], text: '', config: JSON.stringify(resource?.config ?? {
        url: 'https://example.com/', allowed_hosts: ['example.com'], allowed_ports: [443], allow_loopback: false
      }, null, 2), credential: '', clearCredential: false, connectorType: resource?.type === 'mcp' ? 'mcp' : 'http' })
  }
  const saveEditor = async (): Promise<void> => {
    if (!editor) return
    const kind = editor.kind
    const base = { expected_revision: editor.resource?.revision ?? 0, name: editor.name }
    let body: Record<string, unknown> = base
    if (kind === 'agent') body = { ...base, ...(editor.resource ? {} : { workspace_id: workspace }), spec: { position: editor.position, model_slot: editor.slot, skill_ids: editor.skillIds, connector_ids: editor.connectorIds } }
    if (kind === 'skill') body = { ...base, description: editor.description, ...(editor.resource ? {} : { workspace_id: workspace, agent_id: agent, text: editor.text, basis: '用户通过治理界面配置技能正文' }) }
    if (kind === 'connector') body = { ...base, config: JSON.parse(editor.config), ...(editor.resource ? {} : { workspace_id: workspace, type: editor.connectorType }), ...(editor.clearCredential ? { credential: null } : editor.credential ? { credential: editor.credential } : {}) }
    await mutate(`/${kind === 'workspace' ? 'workspaces' : kind === 'agent' ? 'agents' : kind === 'skill' ? 'skills' : 'connectors'}${editor.resource ? '/' + editor.resource.id : ''}`, body, editor.resource ? 'PATCH' : 'POST')
    setEditor(null)
  }
  const statusChange = (kind: string, resource: Resource, status: string): void => {
    setConfirmation({ title: `${stateName(status)}${resource.name}`, description: `对象：${resource.name}，修订${resource.revision}。${status === 'active' ? '重新启用后，当前授权决定可用能力。' : '资源将停止可用，受影响执行取消并保留已发生效果核验；历史记录继续保留。'}`,
      execute: async () => { await mutate(`/${kind}/${resource.id}`, { expected_revision: resource.revision, status }, 'PATCH') } })
  }
  const readVersions = async (resource: Resource): Promise<void> => {
    const generation = viewGeneration.current
    setVersionSkill(resource)
    const items = await allPages<Version>(`/skills/${resource.id}/versions`)
    if (generation !== viewGeneration.current) return
    setVersions(items); setSelectedVersion(items[0] ?? null); setVersionText(items[0]?.content ?? ''); setVersionBasis('')
  }
  const publishVersion = async (restore = false): Promise<void> => {
    if (!versionSkill || !selectedVersion || !versions[0]) return
    await mutate(`/skills/${versionSkill.id}/versions`, { expected_revision: versions[0].version_no, basis: versionBasis,
      ...(restore ? { restore_version_id: selectedVersion.id } : { text: versionText }) })
    await readVersions(versionSkill)
  }
  const auditPath = (after?: string): string => '/audit?' + new URLSearchParams({ limit: '50', ...(actorFilter ? { actor: actorFilter } : {}),
    ...(actionFilter ? { action: actionFilter } : {}), ...(resourceFilter ? { resource: resourceFilter } : {}), ...(after ? { after } : {}) })
  const queryAudit = async (next = false): Promise<void> => {
    const generation = viewGeneration.current
    const page = await api<Page<AuditRow>>(auditPath(next && auditAfter ? auditAfter : undefined))
    if (generation !== viewGeneration.current) return
    setAudit(next ? [...audit, ...page.items] : page.items); setAuditAfter(page.next_after)
  }
  const verifyAudit = async (): Promise<void> => {
    const result = await api<AuditVerification>('/audit/verify', {})
    setVerification(result)
    const status = await api<Diagnostic>('/diagnostics')
    onDiagnostic(status)
    if (status.mode === 'diagnostic') { setResources({ workspaces: [], agents: [], skills: [], connectors: [] }); setGrants([]); setRules([]); setMembers([]); setTab('audit') }
  }
  const restartRestored = async (): Promise<void> => {
    await window.agentcrew.restartBackend()
    const actual = await api<Diagnostic>('/diagnostics')
    const checked = await api<AuditVerification>('/audit/verify', {})
    setVerification(checked); onDiagnostic(actual)
    if (actual.mode !== 'normal' || !checked.ok) throw new Error('恢复后的完整验证失败，业务继续保持诊断状态')
    setRestorePending(false)
  }
  const exportAudit = async (): Promise<void> => {
    const rows = await allPages<AuditRow>(auditPath().replace('/audit?', '/audit/export?'))
    const blob = new Blob([JSON.stringify({ identity: identity.effective_user_id, exported_at: new Date().toISOString(), rows }, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a'); link.href = url; link.download = 'AgentCrew-audit.json'
    document.body.append(link); link.click(); link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const empty = <p className="governance-empty">当前范围没有可见记录。</p>
  const membersPanel = <>
    <h2>组织成员及工作区访问</h2>{!canOwn && <p>角色和工作区成员资格由owner管理。</p>}
    <table className="governance-table"><thead><tr><th>成员</th><th>角色与状态</th><th>当前工作区访问</th></tr></thead><tbody>
      {members.map((member) => <tr key={member.id}>
        <td>{member.name}<small>{member.user_id}</small></td>
        <td><select aria-label={`${member.name}角色`} value={member.role} disabled={!canOwn || busy} onChange={(e) => {
          const role = e.target.value
          setConfirmation({ title: '修改成员角色', description: `将${member.name}角色改为${role}，所有执行会复查当前权限。`,
            execute: async () => { await mutate(`/memberships/${member.id}/role`, { role, expected_revision: member.revision }, 'PATCH') } })
        }}><option value="owner">owner</option><option value="admin">admin</option><option value="member">member</option></select>
          <small>{member.status} · 修订{member.revision}</small>
          <Button danger={member.status === 'active'} disabled={!canOwn || busy} onClick={() => setConfirmation({ title: member.status === 'active' ? '禁用成员资格' : '启用成员资格',
            description: `成员：${member.name}。禁用会终止当前资格及相关执行，最后一名有效owner受到服务端保护。`,
            execute: async () => { await mutate(`/memberships/${member.id}/role`, { role: member.role, status: member.status === 'active' ? 'disabled' : 'active', expected_revision: member.revision }, 'PATCH') } })}>{member.status === 'active' ? '禁用成员' : '启用成员'}</Button>
        </td>
        <td>{member.workspace_ids.includes(workspace) ? '已登记' : '未登记'}
          <Button disabled={!canOwn || busy || !workspace} onClick={() => void operate(async () => {
            const access = member.workspace_access.find((item) => item.workspace_id === workspace)
            await mutate(`/workspaces/${workspace}/members/${member.user_id}`, { enabled: !access?.enabled, expected_revision: access?.revision ?? 0 })
          }, '工作区成员状态已保存')}>{member.workspace_ids.includes(workspace) ? '回收访问' : '登记访问'}</Button>
        </td>
      </tr>)}
    </tbody></table>
  </>
  const organizationPanel = <>
    <h2>{organization?.name ?? '组织'}</h2><p>组织状态：{stateName(organization?.status ?? '')} · 修订{organization?.revision}</p>
    <Field label="组织名称"><input value={organizationName} disabled={!canOwn || busy} onChange={(e) => setOrganizationName(e.target.value)} /></Field>
    {canOwn && organization && <div className="governance-actions">
      <Button disabled={busy || !organizationName.trim()} onClick={() => setConfirmation({ title: '修改组织名称', description: `保存组织名称${organizationName}，当前修订${organization.revision}。`,
        execute: async () => { await mutate('/organization', { expected_revision: organization.revision, name: organizationName }, 'PATCH') } })}>保存组织配置</Button>
      <Button danger={organization.status === 'active'} disabled={busy} onClick={() => setConfirmation({ title: organization.status === 'active' ? '禁用组织' : '启用组织',
        description: `组织：${organization.name}。禁用停止全部业务执行，保留治理记录；真实owner可以重新启用。`,
        execute: async () => { await mutate('/organization', { expected_revision: organization.revision, status: organization.status === 'active' ? 'disabled' : 'active' }, 'PATCH') } })}>{organization.status === 'active' ? '禁用组织' : '启用组织'}</Button>
    </div>}
    <div className="governance-heading"><h2>已登记工作区</h2><Button disabled={!canManage || busy} onClick={() => edit('workspace', null)}>创建工作区</Button></div>
    {resources.workspaces.length ? dataRows('workspaces', resources.workspaces) : empty}
  </>
  const auditPanel = <>
    <h2>审计查询与两级验证</h2>
    <div className="governance-form-inline">
      <Field label="审计操作者"><input value={actorFilter} onChange={(e) => setActorFilter(e.target.value)} /></Field>
      <Field label="审计动作"><input value={actionFilter} onChange={(e) => setActionFilter(e.target.value)} /></Field>
      <Field label="审计资源"><input value={resourceFilter} onChange={(e) => setResourceFilter(e.target.value)} /></Field>
      <Button disabled={busy} onClick={() => void operate(() => queryAudit(), '已读取实际审计记录')}>查询审计</Button>
      <Button disabled={busy} onClick={() => void operate(exportAudit, '审计已导出')}>导出审计</Button>
      <Button disabled={!canOwn || busy} onClick={() => void operate(verifyAudit, '审计验证已完成')}>验证审计链</Button>
    </div>
    {verification && <section className="governance-verification" role="status">
      <p>内部链：{verification.internal.ok ? '通过' : '失败'}</p><p>锚点：{verification.anchor.ok ? '通过' : '失败'}</p>
      <p>检查记录：{verification.internal.checked_rows} · 断点：{verification.internal.broken_at ?? '没有'} · {verification.internal.reason}</p>
      <p>锚点状态：{verification.anchor.status} · 序号：{verification.anchor.seq ?? '没有'}</p><small>{verification.anchor.hash}</small>
    </section>}
    {audit.length ? <table className="governance-table"><thead><tr><th>审计序号与时间</th><th>操作者与动作</th><th>资源与详情</th></tr></thead><tbody>
      {audit.map((row) => <tr key={row.seq}><td>{row.seq}<small>{stamp(row.ts)}</small></td><td>{row.actor_type} · {row.actor_id}<small>{row.action}</small></td><td>{row.resource_type} · {row.resource_id}<details><summary>审计详情与哈希</summary><pre>{JSON.stringify(row.detail, null, 2)}</pre><small>{row.hash}</small></details></td></tr>)}
    </tbody></table> : empty}
    {auditAfter && <Button disabled={busy} onClick={() => void operate(() => queryAudit(true), '已读取下一页审计')}>读取下一页</Button>}
    <h2>受控备份与恢复</h2>
    <p>备份要求任务、作业、队列和写入意图停止。整目录恢复会恢复配置、资源、授权与文件；数据库备份要求当前文件与快照一致。请将chain-head.txt另行保存到数据目录之外。</p>
    <div className="governance-form-inline"><Field label="备份类型"><select value={backupKind} onChange={(e) => setBackupKind(e.target.value)} disabled={!canOwn}><option value="directory">整目录备份</option><option value="database">数据库备份</option></select></Field>
      <Button disabled={!canOwn || busy || diagnostic.mode !== 'normal'} onClick={() => void operate(async () => { await mutate('/diagnostics/backups', { kind: backupKind }) }, '自洽备份已创建并验证')}>创建验证备份</Button>
    </div>
    {backups.map((backup) => <section className="governance-backup" key={backup.id}><strong>{backup.kind === 'directory' ? '整目录备份' : '数据库备份'} · {stamp(backup.created_at)}</strong><small>{backup.id}</small><p>{backup.verified ? '已验证' : '验证失败'} · 锚点序号{backup.anchor_seq}</p>
      <Button danger disabled={!canOwn || busy || !backup.verified || diagnostic.mode !== 'diagnostic'} onClick={() => setConfirmation({ title: '恢复验证备份', description: `恢复${backup.id}，保留当前损坏数据库及文件。提交后仍处于诊断状态，重启并通过完整验证后恢复业务。`, execute: async () => { await mutate('/diagnostics/restore', { backup_id: backup.id }); setRestorePending(true) } })}>恢复此备份</Button>
    </section>)}
    {restorePending && <Button disabled={busy} type="primary" onClick={() => void operate(restartRestored, '重启后的完整验证已经通过')}>重启并完整验证</Button>}
  </>
  function dataRows(kind: string, items: Resource[]): JSX.Element {
    return <table className="governance-table"><thead><tr><th>资源与来源</th><th>状态及修订</th><th>操作</th></tr></thead><tbody>{items.map((resource) => <tr key={resource.id}>
      <td><strong>{resource.name}</strong><small>{resource.id}</small>{resource.description && <p>{resource.description}</p>}<small>来源：{resource.source ?? '人类配置'} · {stamp(resource.created_at)}</small>
        {resource.spec && <p>岗位：{resource.spec.position}<br />模型槽：{resource.spec.model_slot}</p>}
        {typeof resource.credential_configured === 'boolean' && <p>认证凭据：{resource.credential_configured ? '已配置' : '未配置'}</p>}
      </td><td>{stateName(resource.status)} · 修订{resource.revision}</td><td><div className="governance-actions">
        <Button disabled={!canManage || busy} onClick={() => edit(kind === 'workspaces' ? 'workspace' : kind === 'agents' ? 'agent' : kind === 'skills' ? 'skill' : 'connector', resource)}>编辑{kind === 'agents' ? '员工' : kind === 'skills' ? '技能' : kind === 'workspaces' ? '工作区' : '连接器'}</Button>
        {kind === 'skills' && <Button disabled={busy} onClick={() => void operate(() => readVersions(resource), '已读取不可变版本')}>查看版本</Button>}
        {kind === 'connectors' && <Button disabled={!canManage || busy || resource.status !== 'active'} onClick={() => void operate(async () => { const result = await api<Record<string, unknown>>(`/connectors/${resource.id}/validate`, {}); setNote(JSON.stringify(result)) }, '真实连接校验已完成')}>验证连接</Button>}
        <Button disabled={!canManage || busy} danger={resource.status === 'active'} onClick={() => statusChange(kind, resource, resource.status === 'active' ? 'disabled' : 'active')}>{resource.status === 'active' ? '禁用' : '启用'}</Button>
        {kind === 'skills' && resource.status !== 'archived' && <Button disabled={!canManage || busy} danger onClick={() => statusChange(kind, resource, 'archived')}>归档</Button>}
      </div></td>
    </tr>)}</tbody></table>
  }

  const tabs = [
    { key: 'employees', label: '数字员工', disabled: diagnostic.mode === 'diagnostic', children: <><div className="governance-heading"><h2>员工岗位与配置</h2><Button type="primary" disabled={!canManage || busy} onClick={() => edit('agent', null)}>创建员工</Button></div>{resources.agents.length ? dataRows('agents', resources.agents.filter((item) => item.workspace_id === workspace)) : empty}</> },
    { key: 'workspaces', label: '组织与工作区', disabled: diagnostic.mode === 'diagnostic', children: organizationPanel },
    { key: 'skills', label: '技能与版本', disabled: diagnostic.mode === 'diagnostic', children: <><div className="governance-heading"><h2>工作区技能</h2><Button disabled={!canManage || busy || !agent} onClick={() => edit('skill', null)}>创建技能</Button></div>{resources.skills.length ? dataRows('skills', resources.skills.filter((item) => item.workspace_id === workspace)) : empty}</> },
    { key: 'connectors', label: '连接器', disabled: diagnostic.mode === 'diagnostic', children: identity.role === 'member' ? <p>连接器配置由owner或admin管理。员工仅使用当前Grant允许的连接器。</p> : <><div className="governance-heading"><h2>HTTP与MCP连接器</h2><Button disabled={!canManage || busy} onClick={() => edit('connector', null)}>创建连接器</Button></div>{resources.connectors.length ? dataRows('connectors', resources.connectors) : empty}</> },
    { key: 'grants', label: 'Grant授权', disabled: diagnostic.mode === 'diagnostic', children: <><h2>当前与历史授权</h2><div className="governance-form-inline"><Field label="授权资源类型"><select value={grantKind} disabled={!canManage} onChange={(e) => { setGrantKind(e.target.value); setGrantResource(''); setGrantee('') }}><option value="skill">Skill</option><option value="connector">连接器</option><option value="agent">员工使用</option></select></Field><Field label="授权资源"><select value={grantResource} onChange={(e) => setGrantResource(e.target.value)} disabled={!canManage}><option value="">请选择</option>{(grantKind === 'skill' ? resources.skills : grantKind === 'connector' ? resources.connectors : resources.agents).filter((item) => item.workspace_id === workspace && item.status === 'active').map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field><Field label="授权接收者"><select value={grantee} onChange={(e) => setGrantee(e.target.value)} disabled={!canManage}><option value="">请选择</option>{(grantKind === 'agent' ? members.map((item) => ({ id: item.user_id, name: item.name })) : resources.agents.filter((item) => item.workspace_id === workspace && item.status === 'active')).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field><Button disabled={!canManage || busy || !grantResource || !grantee} onClick={() => void operate(async () => { await mutate('/grants', { resource_type: grantKind, resource_id: grantResource, grantee_type: grantKind === 'agent' ? 'user' : 'agent', grantee_id: grantee }) }, '授权已保存')}>授予能力</Button></div>{grants.length ? <table className="governance-table"><thead><tr><th>资源与接收者</th><th>来源及状态</th><th>操作</th></tr></thead><tbody>{grants.map((grant) => <tr key={grant.id}><td>{grant.resource_type} · {grant.resource_id}<small>{grant.grantee_type} · {grant.grantee_id}</small></td><td>{grant.revoked_at ? '已撤销' : '有效'} · 修订{grant.revision}<small>授予者：{grant.granted_by_user_id} · {stamp(grant.created_at)}</small></td><td><Button danger disabled={!canManage || busy || Boolean(grant.revoked_at)} onClick={() => setConfirmation({ title: '撤销授权', description: `撤销${grant.id}后，当前执行立即复查；已发生效果保留核验。`, execute: async () => { await mutate(`/grants/${grant.id}`, { expected_revision: grant.revision }, 'DELETE') } })}>撤销授权</Button></td></tr>)}</tbody></table> : empty}</> },
    { key: 'rules', label: '员工规则', disabled: diagnostic.mode === 'diagnostic', children: <><h2>当前员工权限规则</h2><p>bash按完整命令等值匹配，deny优先；人工审批继续受scope、protected、角色和Grant限制。</p><div className="governance-form-inline"><Field label="规则工具"><input value={ruleTool} onChange={(e) => setRuleTool(e.target.value)} disabled={!canManage} /></Field><Field label="规则匹配范围"><input value={rulePattern} onChange={(e) => setRulePattern(e.target.value)} disabled={!canManage} /></Field><Field label="规则决定"><select value={ruleEffect} onChange={(e) => setRuleEffect(e.target.value)} disabled={!canManage}><option value="allow">允许</option><option value="deny">拒绝</option></select></Field><Button disabled={!canManage || busy || !ruleTool || !rulePattern || !agent} onClick={() => void operate(async () => { await mutate(`/agents/${agent}/permission-rules`, { tool_name: ruleTool, pattern: rulePattern, effect: ruleEffect }) }, '规则已保存')}>保存规则</Button></div>{rules.length ? <table className="governance-table"><thead><tr><th>工具及匹配范围</th><th>决定与来源</th><th>操作</th></tr></thead><tbody>{rules.map((rule) => <tr key={rule.id}><td>{rule.tool_name}<small>{rule.pattern}</small></td><td>{rule.effect} · {rule.revoked_at ? '已回收' : '有效'} · 修订{rule.revision}<small>创建者：{rule.created_by_user_id}</small></td><td><Button danger disabled={!canManage || busy || Boolean(rule.revoked_at)} onClick={() => setConfirmation({ title: '回收权限规则', description: `回收${rule.tool_name}范围${rule.pattern}。后续调用重新判定当前规则。`, execute: async () => { await mutate(`/agents/${agent}/permission-rules/${rule.id}`, { expected_revision: rule.revision }, 'DELETE') } })}>回收规则</Button></td></tr>)}</tbody></table> : empty}</> },
    { key: 'roles', label: '角色与成员', disabled: diagnostic.mode === 'diagnostic', children: membersPanel },
    { key: 'audit', label: '审计与诊断', children: auditPanel }
  ]
  return <main className="governance-page" aria-busy={loading || busy}><div className="governance-heading"><div><h1>员工与治理</h1><p>当前角色：{identity.role} · 有效身份：{identity.name} · 真实凭证所属者：{identity.credential_owner_id}</p></div><Button disabled={busy} onClick={() => void operate(() => load(), '已刷新当前状态')}>刷新治理</Button></div>{diagnostic.mode === 'normal' && <div className="governance-form-inline"><Field label="治理工作区"><select value={workspace} disabled={busy} onChange={(e) => selectScope(e.target.value, resources.agents.find((item) => item.workspace_id === e.target.value && item.status === 'active')?.id ?? '')}>{resources.workspaces.filter((item) => item.status === 'active').map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field><Field label="治理员工"><select value={agent} disabled={busy} onChange={(e) => selectScope(workspace, e.target.value)}>{resources.agents.filter((item) => item.workspace_id === workspace && item.status === 'active').map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field></div>}{diagnostic.mode === 'diagnostic' && <section className="governance-diagnostic" role="alert"><h2>只读诊断</h2><p>{diagnostic.reason}</p><p>{diagnostic.hint}</p></section>}{blockReason && <p className="governance-permission">{blockReason}</p>}{error && <p id="governance-error" role="alert">{error}；未提交的编辑内容保持完整。</p>}{note && <p role="status">{note}</p>}<Tabs activeKey={tab} onChange={setTab} items={tabs} />
    <Modal title={editor ? `${editor.resource ? '编辑' : '创建'}${editor.kind === 'agent' ? '员工' : editor.kind === 'skill' ? '技能' : editor.kind === 'workspace' ? '工作区' : '连接器'}` : ''} open={Boolean(editor)} onCancel={() => { if (!busy) setEditor(null) }} onOk={() => void operate(saveEditor, '资源已保存')} confirmLoading={busy} okText={editor?.kind === 'agent' ? '保存员工' : '保存资源'} cancelText="取消编辑" destroyOnClose>
      {editor && <div className="governance-editor"><Field label={editor.kind === 'agent' ? '员工名称' : '资源名称'}><input autoFocus aria-describedby="governance-error" value={editor.name} onChange={(e) => setEditor({ ...editor, name: e.target.value })} required /></Field>{editor.kind === 'agent' && <><Field label="员工岗位"><textarea value={editor.position} onChange={(e) => setEditor({ ...editor, position: e.target.value })} required /></Field><Field label="员工模型槽"><select value={editor.slot} onChange={(e) => setEditor({ ...editor, slot: e.target.value as 'main' | 'aux' })}><option value="main">main</option><option value="aux">aux</option></select></Field><Field label="员工技能引用"><select multiple value={editor.skillIds} onChange={(e) => setEditor({ ...editor, skillIds: Array.from(e.target.selectedOptions, (option) => option.value) })}>{resources.skills.filter((item) => item.workspace_id === workspace && item.status === 'active').map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field><Field label="员工连接器引用"><select multiple value={editor.connectorIds} onChange={(e) => setEditor({ ...editor, connectorIds: Array.from(e.target.selectedOptions, (option) => option.value) })}>{resources.connectors.filter((item) => item.status === 'active').map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field><p>资源引用保存配置，执行仍需当前有效Grant。</p></>}{editor.kind === 'skill' && <><Field label="技能描述"><input value={editor.description} onChange={(e) => setEditor({ ...editor, description: e.target.value })} required /></Field>{!editor.resource && <Field label="技能正文"><textarea value={editor.text} onChange={(e) => setEditor({ ...editor, text: e.target.value })} required /></Field>}</>}{editor.kind === 'connector' && <><Field label="连接器类型"><select value={editor.connectorType} disabled={Boolean(editor.resource)} onChange={(e) => setEditor({ ...editor, connectorType: e.target.value as 'http' | 'mcp' })}><option value="http">HTTP</option><option value="mcp">MCP</option></select></Field><Field label="连接器配置JSON"><textarea className="governance-code" value={editor.config} onChange={(e) => setEditor({ ...editor, config: e.target.value })} required /></Field><Field label="连接器凭据"><input type="password" autoComplete="off" value={editor.credential} onChange={(e) => setEditor({ ...editor, credential: e.target.value })} /></Field><p>凭据由服务端绑定，保存后仅展示配置状态。</p></>}{error && <p role="alert">{error}</p>}</div>}
      {editor?.kind === 'connector' && editor.resource && <label><input type="checkbox" checked={editor.clearCredential} onChange={(event) => setEditor({ ...editor, clearCredential: event.target.checked })} />清除已配置凭据</label>}
    </Modal>
    <Modal title={confirmation?.title ?? ''} open={Boolean(confirmation)} okText="确认操作" cancelText="取消操作" confirmLoading={busy} onCancel={() => { if (!busy) setConfirmation(null) }} onOk={() => { if (confirmation) void operate(confirmation.execute, '操作已保存') }}><p>{confirmation?.description}</p>{error && <p role="alert">{error}</p>}</Modal>
    <Modal title="不可变技能版本" open={Boolean(versionSkill)} width={760} footer={null} onCancel={() => { if (!busy) setVersionSkill(null) }}>
      <p>技能：{versionSkill?.name}；历史正文保持完整，恢复旧正文产生新版本。</p>
      <Field label="技能历史版本"><select value={selectedVersion?.id ?? ''} disabled={busy || !versions.length} onChange={(e) => { const selected = versions.find((item) => item.id === e.target.value)!; setSelectedVersion(selected); setVersionText(selected.content) }}>{versions.map((item) => <option key={item.id} value={item.id}>版本{item.version_no} · {stamp(item.created_at)} · {item.created_by}</option>)}</select></Field>
      <p>账本：{selectedVersion?.ledger_id} · SHA：{selectedVersion?.sha256}</p>
      <Field label="版本正文"><textarea value={versionText} readOnly={!canManage} disabled={busy || !selectedVersion} onChange={(e) => setVersionText(e.target.value)} /></Field>
      {selectedVersion?.files.map((file) => <details key={file.path}><summary>版本支撑文件：{file.path}</summary><pre>{file.content ?? '文件不存在'}</pre></details>)}
      <Field label="版本修改依据"><input value={versionBasis} disabled={!canManage || busy || !selectedVersion} onChange={(e) => setVersionBasis(e.target.value)} /></Field>
      <div className="governance-actions"><Button disabled={!canManage || busy || !versionBasis.trim()} onClick={() => void operate(() => publishVersion(), '新版本已发布')}>发布新版本</Button><Button disabled={!canManage || busy || !versionBasis.trim()} onClick={() => void operate(() => publishVersion(true), '旧正文已恢复为新版本')}>恢复旧正文为新版本</Button></div>
      {error && <p role="alert">{error}</p>}
    </Modal>
  </main>
}
