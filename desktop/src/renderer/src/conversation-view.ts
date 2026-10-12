import type { Frame } from './session'

export const statusNames: Record<string, string> = {
  loading: '正在读取任务',
  idle: '等待新指令', starting: '正在启动', queued: '排队中', running: '执行中',
  waiting_approval: '等待审批', waiting_question: '需要回答', waiting_user: '等待人工处理',
  waiting_verification: '待核验', completed: '已完成', failed: '执行失败',
  cancelled: '已停止', interrupted: '已中断', retry_wait: '等待重试'
}

export const toolStatusNames: Record<string, string> = {
  'tool.prepared': '等待执行', 'tool.dispatched': '正在执行', 'tool.completed': '已完成',
  'tool.failed': '执行失败', 'tool.skipped_idempotent': '已处理', 'tool.pending_verification': '待核验',
  'tool.verification_submitted': '已核验', 'tool.result_externalized': '结果已保存'
}

export const toolNames: Record<string, string> = {
  read_file: '读取文件', write_file: '写入文件', list_dir: '查看目录', list_directory: '查看目录',
  run_command: '执行命令', execute_command: '执行命令', shell: '执行命令',
  search_files: '查找文件', web_search: '搜索资料', fetch_url: '读取网页'
}

export const jobNames: Record<string, string> = {
  summary: '整理任务记忆', curate: '维护长期记忆', trace: '分析执行记录', review: '核查执行结果'
}

export function currentState(events: Frame[], state: string | undefined, approvals = 0, questions = 0): string {
  if (approvals > 0) return 'waiting_approval'
  if (questions > 0) return 'waiting_question'
  if (state === 'error') return 'failed'
  if (state !== 'idle') return state ?? 'loading'
  const latest = events.filter((event) => ['run.queued', 'run.started', 'run.resumed', 'run.completed', 'run.failed', 'run.cancelled', 'run.interrupted'].includes(event.type)).at(-1)
  return latest && ['run.completed', 'run.failed', 'run.cancelled', 'run.interrupted'].includes(latest.type) ? latest.type.slice(4) : 'idle'
}

export function taskTitle(title: string | null | undefined, instruction: string | undefined): string {
  if (title?.trim()) return title.trim()
  if (instruction?.trim()) {
    const firstLine = instruction.trim().split(/[。\n]/u)[0]
    const characters = Array.from(firstLine)
    return characters.length > 42 ? characters.slice(0, 42).join('') + '…' : firstLine
  }
  return '未命名任务'
}

export function displayTime(timestamp: string): string {
  const date = new Date(timestamp)
  if (date.toDateString() === new Date().toDateString()) return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

export function toolTarget(input: Record<string, unknown>): string | null {
  const target = input.path ?? input.file_path ?? input.command ?? input.url ?? input.query
  return typeof target === 'string' ? target : null
}
