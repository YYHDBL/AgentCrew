import { useEffect, useState } from 'react'
import { Button } from 'antd'
import type { Frame } from '../session'

export function Replay({ events, position, selected, hasMore, busy, change, more, restart }: {
  events: Frame[]; position: number; selected: number; hasMore: boolean; busy: boolean
  change: (seq: number) => void; more: () => Promise<void>; restart?: () => void
}): JSX.Element {
  const [playing, setPlaying] = useState(false)
  const next = events.find((event) => (event.seq ?? 0) > position)
  const step = (): void => {
    if (next?.seq) change(next.seq)
    else if (hasMore) void more()
    else setPlaying(false)
  }
  useEffect(() => {
    if (!playing || busy) return
    const timer = setTimeout(step, 700)
    return () => clearTimeout(timer)
  }, [playing, busy, position, events, hasMore])
  return <section className="replay-controls" aria-label="只读历史回放">
    <div className="governance-actions">
      <Button onClick={() => { setPlaying(false); change(0); restart?.() }}>从头回放</Button>
      <Button disabled={busy || (!next && !hasMore)} onClick={() => setPlaying(!playing)}>{playing ? '暂停回放' : '播放回放'}</Button>
      <Button disabled={busy || (!next && !hasMore)} onClick={() => { setPlaying(false); step() }}>单步前进</Button>
      <Button disabled={busy} onClick={() => { setPlaying(false); change(selected) }}>显示已读取末尾</Button>
      <Button disabled={busy || !hasMore} onClick={() => void more()}>加载后续事件</Button>
    </div>
    <p data-testid="replay-position" role="status">历史位置 seq {position} · 已读取末尾 seq {selected}</p>
    <p className="run-muted">历史内容仅供查阅，审批与执行操作保持禁用。</p>
  </section>
}
