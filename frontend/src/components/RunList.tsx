import { useState } from 'react'
import type { Run } from '../types'
import { Icon } from './icons'
import { relTime, runState } from './ui'

type Filter = 'all' | 'attention' | 'progress' | 'done'

const matches = (r: Run, f: Filter) =>
  f === 'all' ||
  (f === 'attention' && r.status === 'awaiting_approval') ||
  (f === 'progress' && (r.status === 'running' || r.status === 'created')) ||
  (f === 'done' && ['completed', 'failed', 'rejected'].includes(r.status))

const ICON_COLOR: Record<string, string> = {
  draft: 'var(--faint)',
  progress: 'var(--accent)',
  attention: 'var(--warn)',
  merged: 'var(--done)',
  closed: 'var(--bad)',
}

export function RunList({ runs, onOpen, onNew }: { runs: Run[]; onOpen: (id: string) => void; onNew: () => void }) {
  const [filter, setFilter] = useState<Filter>('all')
  const count = (f: Filter) => runs.filter((r) => matches(r, f)).length
  const shown = runs.filter((r) => matches(r, filter))

  if (runs.length === 0) {
    return (
      <div className="card">
        <div className="empty" style={{ padding: '64px 24px' }}>
          <div style={{ color: 'var(--muted)', marginBottom: 12 }}><Icon name="migrate" size={32} /></div>
          <h2 style={{ color: 'var(--text)' }}>Start your first migration</h2>
          <p style={{ margin: '8px auto 16px', maxWidth: 480 }}>
            Point the platform at a COBOL repository. It analyses it, proposes a plan for your approval, translates it
            program by program and proves each unit behaves like the original before anything is committed.
          </p>
          <button className="btn primary" onClick={onNew}><Icon name="plus" /> New migration</button>
        </div>
      </div>
    )
  }

  return (
    <div className="card">
      <div className="list-head">
        <div className="filters" role="tablist" aria-label="Filter migrations">
          {([['all', 'All'], ['attention', 'Needs review'], ['progress', 'In progress'], ['done', 'Closed']] as [Filter, string][]).map(([f, label]) => (
            <button key={f} className={`filter ${filter === f ? 'on' : ''}`} onClick={() => setFilter(f)} role="tab" aria-selected={filter === f}>
              {label} <span className="counter">{count(f)}</span>
            </button>
          ))}
        </div>
      </div>
      {shown.length === 0 && <div className="empty">No migrations match this filter.</div>}
      {shown.map((r) => {
        const st = runState(r.status)
        return (
          <button key={r.id} className="runrow" onClick={() => onOpen(r.id)}>
            <span className="ic" style={{ color: ICON_COLOR[st.cls] }}><Icon name={st.icon} /></span>
            <span className="main-col">
              <span className="t">{r.name}</span>
              <div className="meta">
                #{r.id.slice(-6)} opened {relTime(r.created)} · {st.label.toLowerCase()}
                {r.pending ? ` · ${r.pending.type === 'plan_approval' ? 'plan awaiting your approval' : `${r.pending.program} awaiting review`}` : ''}
                {r.error ? ' · ' + r.error.slice(0, 80) : ''}
              </div>
            </span>
            <span className="side">{r.options.auto_approve_verified && <span className="badge">auto-approve verified</span>}</span>
          </button>
        )
      })}
    </div>
  )
}
