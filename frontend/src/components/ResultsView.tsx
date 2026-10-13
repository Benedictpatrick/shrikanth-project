import { useEffect, useState } from 'react'
import { api } from '../api'
import type { CaseIO, CaseResult, Health, Oracle, Pending, Run, Unit } from '../types'
import { Code, Diff } from './Code'
import { Icon } from './icons'
import { ORACLE_PLAIN } from './ui'

/** Plain-language description of a unit's outcome. */
export function describe(status: string, passed: number, total: number): { tone: 'ok' | 'warn' | 'bad'; title: string; text: string } {
  switch (status) {
    case 'verified':
      return { tone: 'ok', title: 'Matches the original', text: `The migrated Python produced exactly the same output as the original on all ${total} test case${total === 1 ? '' : 's'}.` }
    case 'provisional':
      return { tone: 'warn', title: 'Matches, but unconfirmed', text: `It matches ${passed}/${total} expected outputs that were predicted by AI, not produced by the original program. Treat as unconfirmed.` }
    case 'failed':
      return { tone: 'bad', title: 'Differs from the original', text: `The output differs from the original in ${total - passed} of ${total} test case${total === 1 ? '' : 's'}. See the highlighted differences below.` }
    case 'blocked':
      return { tone: 'bad', title: 'Not fully translated', text: 'Some statements could not be translated automatically. Add a Groq API key in Settings so the AI can translate them, or finish them by hand.' }
    default:
      return { tone: 'warn', title: 'Not compared', text: 'There is no test data for this program, so its behaviour could not be compared with the original.' }
  }
}

const TONE_COLOR = { ok: 'var(--ok)', warn: 'var(--warn)', bad: 'var(--bad)' } as const
const TONE_ICON = { ok: 'check', warn: 'alert', bad: 'x' } as const

function OutputPanel({ title, io, empty }: { title: string; io: CaseIO; empty?: string }) {
  const files = Object.entries(io.files)
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="eyebrow">{title}</div>
      <div>
        <div className="small muted" style={{ marginBottom: 4 }}>Console output</div>
        {io.stdout ? <Code text={io.stdout.replace(/\n$/, '')} lang="plain" maxHeight={220} /> : <div className="muted small">{empty ?? '(nothing printed)'}</div>}
      </div>
      {files.map(([name, text]) => (
        <div key={name}>
          <div className="small muted" style={{ marginBottom: 4 }}>File <code>{name}</code></div>
          {text ? <Code text={text.replace(/\n$/, '')} lang="plain" maxHeight={260} /> : <div className="muted small">(empty file)</div>}
        </div>
      ))}
      {files.length === 0 && !io.stdout && null}
    </div>
  )
}

function CaseBlock({ c, defaultOpen }: { c: CaseResult; defaultOpen: boolean }) {
  const inputs = Object.entries(c.inputs ?? {})
  return (
    <details className="case" open={defaultOpen}>
      <summary>
        <span style={{ color: c.passed ? 'var(--ok)' : 'var(--bad)' }}><Icon name={c.passed ? 'check' : 'x'} /></span>
        <b>{c.name}</b>
        <span className="muted small" style={{ flex: 1 }}>{c.description}</span>
        <span className={`badge ${c.passed ? 'ok' : 'bad'}`}>{c.passed ? 'same output' : 'different output'}</span>
      </summary>
      <div className="body">
        <div className="stack" style={{ gap: 8 }}>
          <div className="eyebrow">1 · Input given to both programs</div>
          {inputs.length === 0 && <span className="muted small">No input files.</span>}
          {inputs.map(([name, text]) => (
            <div key={name}>
              <div className="small muted" style={{ marginBottom: 4 }}>File <code>{name}</code></div>
              {text ? <Code text={text.replace(/\n$/, '')} lang="plain" maxHeight={200} /> : <div className="muted small">(empty file)</div>}
            </div>
          ))}
        </div>
        <div className="split">
          <OutputPanel title="2 · Original program produced" io={c.expected} />
          <OutputPanel title="3 · Migrated Python produced" io={c.actual} empty={c.passed ? undefined : '(nothing - the program failed, see below)'} />
        </div>
        {c.error && <div className="notice bad mono small" style={{ whiteSpace: 'pre-wrap' }}>{c.error}</div>}
        {c.diff.length > 0 && (
          <div className="stack" style={{ gap: 6 }}>
            <div className="eyebrow">What differs (<span style={{ color: 'var(--bad)' }}>-</span> original, <span style={{ color: 'var(--ok)' }}>+</span> migrated)</div>
            <Diff lines={c.diff} />
          </div>
        )}
        {c.passed && <div className="muted small">The two outputs are identical, character for character.</div>}
      </div>
    </details>
  )
}

export function ResultsView({ run, health, focus, onFocus, onDecide, onGoPlan }: {
  run: Run
  health: Health | null
  focus: string | null
  onFocus: (p: string) => void
  onDecide: (program: string, decision: 'approve' | 'reject' | 'retry', force: boolean) => Promise<void>
  onGoPlan: () => void
}) {
  const programs = Object.keys(run.units)
  const pendingProg = run.pending?.type === 'unit_approval' ? run.pending.program! : null
  const current = focus && run.units[focus] ? focus : pendingProg ?? programs[0]

  if (!programs.length) {
    return (
      <div className="card">
        <div className="empty">
          <div style={{ marginBottom: 8 }}><Icon name="migrate" size={32} /></div>
          <h2 style={{ color: 'var(--text)' }}>No results yet</h2>
          <p style={{ margin: '8px auto 16px', maxWidth: 460 }}>
            {run.pending?.type === 'plan_approval'
              ? 'Results appear here as soon as you approve the plan: for each program you will see the input data, what the original produced and what the migrated Python produced.'
              : run.status === 'running' ? 'The analysis is still running. Results appear after the plan has been approved.' : 'Nothing has been migrated in this run.'}
          </p>
          {run.pending?.type === 'plan_approval' && <button className="btn primary" onClick={onGoPlan}>Review the plan</button>}
        </div>
      </div>
    )
  }

  return (
    <div className="stack">
      <div className="grid-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
        {programs.map((p) => {
          const u = run.units[p]
          const d = describe(u.status, u.passed, u.total)
          const waiting = pendingProg === p
          return (
            <button key={p} className="choice" onClick={() => onFocus(p)} style={{ textAlign: 'left', gridTemplateColumns: 'auto 1fr',
              ...(p === current ? { borderColor: 'var(--accent-emphasis)', boxShadow: '0 0 0 1px var(--accent-emphasis)' } : {}) }}>
              <span style={{ color: TONE_COLOR[d.tone], paddingTop: 2 }}><Icon name={TONE_ICON[d.tone]} /></span>
              <span>
                <b>{p}</b>
                <span className="muted small" style={{ display: 'block' }}>{d.title}</span>
                <span className="muted small" style={{ display: 'block' }}>
                  {u.total ? `${u.passed} of ${u.total} test cases match` : 'no test cases'}
                  {waiting ? ' · waiting for your decision' : u.decision ? (u.decision.decision === 'approve' ? ' · saved to output' : ' · not saved') : ''}
                </span>
              </span>
            </button>
          )
        })}
      </div>
      <UnitPanel key={current} runId={run.id} program={current} pending={run.pending} llm={!!health?.llm.available} onDecide={onDecide} />
    </div>
  )
}

function UnitPanel({ runId, program, pending, llm, onDecide }: {
  runId: string
  program: string
  pending: Pending | null
  llm: boolean
  onDecide: (program: string, decision: 'approve' | 'reject' | 'retry', force: boolean) => Promise<void>
}) {
  const [unit, setUnit] = useState<Unit | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [view, setView] = useState<'split' | 'python' | 'cobol'>('split')

  useEffect(() => {
    api.unit(runId, program).then(setUnit).catch((e) => setErr(e.message))
  }, [runId, program, pending?.type, pending?.program])

  if (err) return <div className="notice bad">{err}</div>
  if (!unit) return <div className="empty">Loading…</div>

  const awaiting = pending?.type === 'unit_approval' && pending.program === program
  const v = unit.verification
  const d = describe(unit.status, v.passed, v.total)
  const unsafe = unit.status === 'blocked' || unit.status === 'failed'
  const stubLines = unit.code.split('\n').flatMap((l, i) => (l.includes('NotImplementedError') ? [i + 1] : []))
  const act = async (dec: 'approve' | 'reject' | 'retry', force = false) => {
    setBusy(true)
    try { await onDecide(program, dec, force) } finally { setBusy(false) }
  }
  const firstFail = v.results.findIndex((r) => !r.passed)

  return (
    <div className="stack">
      <div className={`notice ${d.tone}`} style={{ alignItems: 'center' }}>
        <span style={{ color: TONE_COLOR[d.tone] }}><Icon name={TONE_ICON[d.tone]} size={20} /></span>
        <div style={{ flex: 1 }}>
          <b>{program}: {d.title}</b>
          <div className="small">{d.text}</div>
          <div className="small muted">Compared against: {ORACLE_PLAIN[unit.oracle as Oracle]}.</div>
        </div>
      </div>

      {awaiting && (
        <div className="approval">
          <span className="ap-ic"><Icon name="alert" size={20} /></span>
          <div style={{ flex: 1, minWidth: 240 }}>
            <b>Your decision</b>
            <div className="muted small">
              {unsafe
                ? 'This program does not match the original yet, so it will not be saved unless you choose "Save anyway".'
                : 'Approving saves the migrated program, its tests and its documentation to the output repository.'}
            </div>
          </div>
          <div className="row">
            {!unsafe && <button className="btn primary" disabled={busy} onClick={() => act('approve')}>Approve &amp; save</button>}
            {llm && <button className="btn" disabled={busy} onClick={() => act('retry')}>Try again with AI</button>}
            <button className="btn danger" disabled={busy} onClick={() => act('reject')}>Reject</button>
            {unsafe && <button className="btn ghost" disabled={busy} onClick={() => act('approve', true)}>Save anyway</button>}
          </div>
        </div>
      )}
      {!awaiting && unit.decision && (
        <div className={`notice ${unit.decision.decision === 'approve' ? 'ok' : 'warn'}`}>
          {unit.decision.decision === 'approve'
            ? <>Saved to the output repository{unit.decision.auto ? ' automatically' : ''}{unit.decision.commit ? <> (commit <code>{unit.decision.commit}</code>)</> : null}. Find it under <b>Output files</b>.</>
            : <>Not saved. {unit.decision.note}</>}
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <h3>Inputs and outputs</h3>
          <span className="muted small">{v.total} test case{v.total === 1 ? '' : 's'} · open one to see the data</span>
        </div>
        <div className="card-pad stack" style={{ gap: 8 }}>
          {v.results.length === 0 && <span className="muted">No test cases were available, so there is nothing to compare.</span>}
          {v.results.map((r, i) => <CaseBlock key={r.name} c={r} defaultOpen={firstFail >= 0 ? i === firstFail : i === 0} />)}
        </div>
      </div>

      <details className="card">
        <summary className="card-head" style={{ cursor: 'pointer' }}>
          <h3>Migrated code {stubLines.length > 0 && <span className="badge bad">{stubLines.length} part(s) not translated</span>}</h3>
          <span className="muted small">{unit.module}.py</span>
        </summary>
        <div className="card-pad stack">
          <div className="row">
            {(['split', 'cobol', 'python'] as const).map((m) => (
              <button key={m} className={`btn sm ${view === m ? 'sel' : ''}`} onClick={() => setView(m)}>{{ split: 'Side by side', cobol: 'Original COBOL', python: 'Migrated Python' }[m]}</button>
            ))}
          </div>
          <div className={view === 'split' ? 'split' : ''}>
            {view !== 'python' && <div><div className="split-title"><b className="small">Original COBOL</b></div><Code text={unit.cobol} lang="cobol" maxHeight={560} /></div>}
            {view !== 'cobol' && <div><div className="split-title"><b className="small">Migrated Python</b></div><Code text={unit.code} lang="py" highlight={stubLines} maxHeight={560} /></div>}
          </div>
        </div>
      </details>

      <details className="card">
        <summary className="card-head" style={{ cursor: 'pointer' }}>
          <h3>How it was translated</h3>
          <span className="muted small">{unit.attempts.length} attempt{unit.attempts.length === 1 ? '' : 's'}</span>
        </summary>
        <div className="card-pad grid-2">
          <div className="timeline">
            {unit.attempts.map((a) => (
              <div key={a.number} className="attempt">
                <span className="n">{a.number}</span>
                <div><b>{a.source === 'rules' ? 'Automatic translator' : 'AI repair'}</b><div className="muted small">{a.note}</div></div>
                <span className={`badge ${a.syntax_error ? 'bad' : a.total && a.failed === 0 ? 'ok' : a.total ? 'bad' : ''}`}>{a.total ? `${a.passed}/${a.total}` : 'n/a'}</span>
              </div>
            ))}
          </div>
          <div>
            <div className="eyebrow" style={{ marginBottom: 6 }}>Statements the translator could not handle on its own</div>
            {unit.issues.length === 0 ? <span className="muted">None.</span> : (
              <ul style={{ margin: 0, paddingLeft: 18 }}>{unit.issues.map((i, k) => <li key={k} className="small">{i.message}</li>)}</ul>
            )}
            {unit.notes.map((n, i) => <div key={i} className="notice warn small" style={{ marginTop: 8 }}>{n}</div>)}
          </div>
        </div>
      </details>
    </div>
  )
}
