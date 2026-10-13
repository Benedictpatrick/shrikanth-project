import { useEffect, useState } from 'react'
import type { Plan, Pending } from '../types'
import { OracleBadge } from './ui'

export function PlanView({
  plan,
  pending,
  onDecide,
}: {
  plan: Plan
  pending: Pending | null
  onDecide: (approved: boolean, programs: string[]) => Promise<void>
}) {
  const waiting = pending?.type === 'plan_approval'
  const [include, setInclude] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setInclude(Object.fromEntries(plan.units.map((u) => [u.program, true])))
  }, [plan])

  const chosen = plan.units.filter((u) => include[u.program]).map((u) => u.program)
  const decide = async (approved: boolean) => {
    setBusy(true)
    try { await onDecide(approved, chosen) } finally { setBusy(false) }
  }

  return (
    <div className="stack">
      {waiting && (
        <div className="approval">
          <div style={{ flex: 1, minWidth: 240 }}>
            <b>Your approval is required before any code is generated.</b>
            <div className="muted small">Review the order, risks and baselines below. Untick programs you do not want migrated in this run.</div>
          </div>
        </div>
      )}
      <div className="card card-pad stack">
        <div className="eyebrow">Strategy</div>
        <p>{plan.summary}</p>
        <p className="muted small">{plan.approach}</p>
        <div className="grid-3">
          <div><div className="eyebrow">Target</div><div>{plan.target.language}</div></div>
          <div><div className="eyebrow">Runtime</div><div className="small">{plan.target.runtime}</div></div>
          <div><div className="eyebrow">Layout</div><div className="small">{plan.target.layout}</div></div>
        </div>
      </div>
      <div className="card">
        <div className="card-head"><h3>Migration order</h3><span className="muted small">dependencies first, simplest first</span></div>
        <div className="table-wrap">
          <table>
            <thead><tr>{waiting && <th />}<th>#</th><th>Program</th><th>Translation</th><th>Baseline</th><th>Risks</th></tr></thead>
            <tbody>
              {plan.units.map((u) => (
                <tr key={u.program}>
                  {waiting && (
                    <td><input type="checkbox" aria-label={`Include ${u.program}`} checked={!!include[u.program]} onChange={(e) => setInclude({ ...include, [u.program]: e.target.checked })} style={{ accentColor: 'var(--accent)' }} /></td>
                  )}
                  <td className="muted">{u.order}</td>
                  <td><b>{u.program}</b><div className="muted small mono">{u.loc} lines · complexity {u.complexity}</div>
                    {u.depends_on.length > 0 && <div className="muted small">after {u.depends_on.join(', ')}</div>}
                    {u.rationale && <div className="muted small" style={{ marginTop: 4 }}>{u.rationale}</div>}</td>
                  <td>{u.translation === 'rules' ? <span className="badge ok">Rule-based</span> : <span className="badge warn">Rules + LLM</span>}</td>
                  <td><OracleBadge oracle={u.oracle} /><div className="muted small">{u.test_cases} case(s)</div></td>
                  <td className="small">{u.risks.length ? <ul style={{ margin: 0, paddingLeft: 16 }}>{u.risks.map((r, i) => <li key={i}>{r}</li>)}</ul> : <span className="muted">none identified</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="card">
        <div className="card-head"><h3>Decisions made</h3></div>
        <div className="card-pad stack">
          {plan.decisions.map((d) => (
            <div key={d.id}><b>{d.question}:</b> {d.choice}
              <div className="muted small">Alternatives considered: {d.alternatives.join('; ')}</div></div>
          ))}
        </div>
      </div>
      {waiting && (
        <div className="sticky-bar">
          <div className="row">
            <button className="btn primary" disabled={busy || chosen.length === 0} onClick={() => decide(true)}>Approve plan ({chosen.length} unit{chosen.length === 1 ? '' : 's'})</button>
            <button className="btn danger" disabled={busy} onClick={() => decide(false)}>Reject</button>
          </div>
        </div>
      )}
    </div>
  )
}
