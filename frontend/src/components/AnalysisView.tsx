import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Analysis, Finding, ProgramAnalysis } from '../types'
import { Code } from './Code'
import { Graph } from './Graph'
import { Markdown } from './Markdown'
import { Kpi, OracleBadge, SeverityBadge } from './ui'

export function ArchitectureView({ analysis, selected, onSelect }: { analysis: Analysis; selected: string | null; onSelect: (k: string) => void }) {
  const progs = Object.values(analysis.programs)
  const sum = (f: (p: ProgramAnalysis) => number) => progs.reduce((a, p) => a + f(p), 0)
  return (
    <div className="stack">
      <div className="kpis">
        <Kpi value={analysis.project.programs} label="Programs" />
        <Kpi value={analysis.project.copybooks.length} label="Copybooks" />
        <Kpi value={analysis.project.loc.toLocaleString()} label="Lines of code" />
        <Kpi value={sum((p) => p.metrics.paragraphs)} label="Paragraphs" />
        <Kpi value={sum((p) => p.metrics.unsupported)} label="Constructs needing LLM" />
        <Kpi value={analysis.security.findings.length} label="Security findings" />
      </div>
      <div className="card">
        <div className="card-head">
          <h3>Dependency graph</h3>
          <div className="legend">
            <span><i style={{ borderColor: 'var(--line-strong)' }} />uses / reads / writes</span>
            <span><i style={{ borderColor: 'var(--accent)' }} />CALL</span>
          </div>
        </div>
        <div className="card-pad">
          <Graph nodes={analysis.graph.nodes} edges={analysis.graph.edges} selected={selected} onSelect={onSelect} />
        </div>
      </div>
      <div className="card">
        <div className="card-head"><h3>Programs</h3></div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Program</th><th>Lines</th><th>Paragraphs</th><th>Complexity</th><th>Files</th><th>Unsupported</th><th>Baseline</th><th /></tr></thead>
            <tbody>
              {Object.entries(analysis.programs).map(([key, p]) => (
                <tr key={key}>
                  <td><b>{p.program_id}</b><div className="muted small mono">{p.path}</div></td>
                  <td>{p.metrics.loc}</td>
                  <td>{p.metrics.paragraphs}</td>
                  <td>{p.metrics.complexity}</td>
                  <td>{p.metrics.files}</td>
                  <td>{p.metrics.unsupported ? <span className="badge warn">{p.metrics.unsupported}</span> : <span className="muted">0</span>}</td>
                  <td><OracleBadge oracle={p.baseline.oracle} /></td>
                  <td><button className="btn sm" onClick={() => onSelect(key)}>Inspect</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

type PTab = 'rules' | 'structure' | 'baseline' | 'docs' | 'source'

export function ProgramsView({ runId, analysis, selected, onSelect }: { runId: string; analysis: Analysis; selected: string | null; onSelect: (k: string) => void }) {
  const keys = Object.keys(analysis.programs)
  const key = selected && analysis.programs[selected] ? selected : keys[0]
  const p = analysis.programs[key]
  const [tab, setTab] = useState<PTab>('rules')
  const [source, setSource] = useState<string | null>(null)

  useEffect(() => {
    setSource(null)
    if (tab === 'source') api.source(runId, p.path).then(setSource).catch((e) => setSource(`// ${e.message}`))
  }, [tab, key, runId, p.path])

  return (
    <div className="stack">
      <div className="row">
        {keys.map((k) => (
          <button key={k} className={`btn ${k === key ? 'sel' : ''}`} onClick={() => onSelect(k)}>{analysis.programs[k].program_id}</button>
        ))}
      </div>
      <div className="kpis">
        <Kpi value={p.metrics.loc} label="Lines" />
        <Kpi value={p.metrics.paragraphs} label="Paragraphs" />
        <Kpi value={p.metrics.statements} label="Statements" />
        <Kpi value={p.metrics.complexity} label="Cyclomatic complexity" />
        <Kpi value={`${Math.round(p.metrics.translatable * 100)}%`} label="Rule-translatable" />
      </div>
      <div className="tabs" role="tablist">
        {(['rules', 'structure', 'baseline', 'docs', 'source'] as PTab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
            {{ rules: 'Business rules', structure: 'Structure', baseline: 'Baseline tests', docs: 'Documentation', source: 'COBOL source' }[t]}
            {t === 'rules' && <span className="count">{p.rules.rules.length}</span>}
          </button>
        ))}
      </div>
      {tab === 'rules' && <RulesPanel p={p} />}
      {tab === 'structure' && <StructurePanel p={p} />}
      {tab === 'baseline' && <BaselinePanel p={p} />}
      {tab === 'docs' && <div className="card card-pad"><Markdown text={p.doc || '_Documentation is being generated…_'} /></div>}
      {tab === 'source' && (source === null ? <div className="empty">Loading…</div> : <Code text={source} lang="cobol" maxHeight={640} />)}
    </div>
  )
}

function RulesPanel({ p }: { p: ProgramAnalysis }) {
  return (
    <div className="stack">
      {p.rules.summary && <div className="notice info"><div><b>{p.rules.used_llm ? 'Interpreted by the LLM' : 'Static analysis'}:</b> {p.rules.summary}</div></div>}
      <div className="card table-wrap">
        <table>
          <thead><tr><th style={{ width: 90 }}>Category</th><th>Rule</th><th style={{ width: 210 }}>Where</th></tr></thead>
          <tbody>
            {p.rules.rules.map((r) => (
              <tr key={r.id}>
                <td><span className="badge neutral">{r.category}</span></td>
                <td><b>{r.title}</b><div className="muted">{r.description}</div></td>
                <td className="mono small muted">{r.paragraph}<div>{r.file}:{r.line}</div></td>
              </tr>
            ))}
            {!p.rules.rules.length && <tr><td colSpan={3} className="muted">No rules detected.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function StructurePanel({ p }: { p: ProgramAnalysis }) {
  const m = p.model
  const top = m.data.filter((d) => d.level === 1 || d.level === 77)
  return (
    <div className="stack">
      <div className="grid-2">
        <div className="card">
          <div className="card-head"><h3>Files</h3></div>
          <div className="table-wrap"><table>
            <thead><tr><th>Name</th><th>Assigned to</th><th>Status</th></tr></thead>
            <tbody>{m.files.map((f) => (
              <tr key={f.name}><td className="mono">{f.name}<div className="muted small">{f.organization} · {f.records.join(', ')}</div></td>
                <td className="mono">{f.assign}</td>
                <td>{f.status ? <code>{f.status}</code> : <span className="badge warn">none</span>}</td></tr>
            ))}</tbody>
          </table></div>
        </div>
        <div className="card">
          <div className="card-head"><h3>Paragraph flow</h3></div>
          <div className="card-pad stack" style={{ gap: 6 }}>
            {m.paragraphs.map((para) => (
              <div key={para.name} className="row" style={{ gap: 8 }}>
                <code>{para.name}</code>
                <span className="faint small">L{para.line}–{para.end_line}</span>
                {(m.paragraph_calls[para.name] ?? []).length > 0 && (
                  <span className="muted small">→ {(m.paragraph_calls[para.name] ?? []).join(', ')}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="card">
        <div className="card-head"><h3>Data division</h3><span className="muted small">{m.data.length} items · top-level shown first</span></div>
        <div className="table-wrap" style={{ maxHeight: 360, overflow: 'auto' }}>
          <table>
            <thead><tr><th>Level</th><th>Name</th><th>Picture</th><th>Usage</th><th>Value / notes</th></tr></thead>
            <tbody>
              {m.data.map((d, i) => (
                <tr key={i}>
                  <td className="mono">{String(d.level).padStart(2, '0')}</td>
                  <td className="mono" style={{ paddingLeft: 12 + (d.level > 1 && d.level < 77 ? Math.min(d.level, 20) * 3 : 0) }}>{d.name}</td>
                  <td className="mono">{d.pic ?? <span className="faint">group</span>}</td>
                  <td>{d.pic ? d.usage : ''}</td>
                  <td className="mono small">{d.value ?? ''}{d.occurs ? ` OCCURS ${d.occurs}` : ''}{d.redefines ? ` REDEFINES ${d.redefines}` : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {top.length === 0 && null}
      {m.unsupported.length > 0 && (
        <div className="card">
          <div className="card-head"><h3>Constructs outside the rule-based subset</h3></div>
          <div className="table-wrap"><table>
            <thead><tr><th>Where</th><th>Construct</th><th>Source</th></tr></thead>
            <tbody>{m.unsupported.map((u, i) => (
              <tr key={i}><td className="mono small">{u.file}:{u.line}</td><td>{u.construct}</td><td className="mono small muted">{u.text}</td></tr>
            ))}</tbody>
          </table></div>
        </div>
      )}
    </div>
  )
}

function BaselinePanel({ p }: { p: ProgramAnalysis }) {
  const b = p.baseline
  return (
    <div className="stack">
      <div className="row"><OracleBadge oracle={b.oracle} /><span className="muted small">{b.cases.length} test case(s)</span></div>
      {b.notes.map((n, i) => <div key={i} className="notice warn">{n}</div>)}
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Case</th><th>Inputs</th><th>Expected outputs</th><th>Origin</th></tr></thead>
          <tbody>{b.cases.map((c) => (
            <tr key={c.name}>
              <td><b>{c.name}</b><div className="muted small">{c.description}</div></td>
              <td className="mono small">{c.inputs.join(', ')}</td>
              <td className="mono small">stdout{c.expected_files.length ? ', ' + c.expected_files.join(', ') : ''}</td>
              <td><span className="badge neutral">{c.source}</span></td>
            </tr>
          ))}
          {!b.cases.length && <tr><td colSpan={4} className="muted">No cases. Add <code>tests/{p.program_id}.json</code> to the repo, install GnuCOBOL (<code>cobc</code>) or configure an LLM.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function SecurityView({ findings, counts }: { findings: Finding[]; counts: { high: number; medium: number; low: number } }) {
  return (
    <div className="stack">
      <div className="kpis">
        <Kpi value={counts.high} label="High" />
        <Kpi value={counts.medium} label="Medium" />
        <Kpi value={counts.low} label="Low" />
      </div>
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Severity</th><th>Finding</th><th>Where</th><th>Recommendation</th></tr></thead>
          <tbody>
            {findings.map((f, i) => (
              <tr key={i}>
                <td><SeverityBadge severity={f.severity} /></td>
                <td><b>{f.title}</b> <span className="faint small">{f.rule}</span><div className="muted">{f.detail}</div></td>
                <td className="mono small">{f.program}<div className="muted">{f.file}:{f.line}</div></td>
                <td className="muted">{f.recommendation}</td>
              </tr>
            ))}
            {!findings.length && <tr><td colSpan={4} className="muted">No findings.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
