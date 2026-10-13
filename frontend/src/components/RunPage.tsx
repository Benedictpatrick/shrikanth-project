import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { Analysis, Health, Plan, Run, RunEvent } from '../types'
import { ArchitectureView, ProgramsView, SecurityView } from './AnalysisView'
import { Icon } from './icons'
import { OutputView } from './OutputView'
import { PlanView } from './PlanView'
import { ResultsView } from './ResultsView'
import { RunState } from './ui'

type Tab = 'overview' | 'plan' | 'results' | 'output' | 'analysis'
type AnalysisTab = 'architecture' | 'programs' | 'security'

const GUIDE = [
  { title: 'Analyze', desc: 'We read your COBOL code' },
  { title: 'Approve plan', desc: 'You check order and risks' },
  { title: 'Review results', desc: 'Original vs migrated output' },
]

/** 0-based index of the step the user is on; 3 means everything is finished. */
function guideStep(run: Run): number {
  if (run.status === 'completed' || run.status === 'rejected') return 3
  if (run.pending?.type === 'plan_approval') return 1
  if (run.pending?.type === 'unit_approval' || Object.keys(run.units).length > 0 || run.stage.startsWith('migrate')
    || ['commit', 'approve', 'finalize'].includes(run.stage)) return 2
  return 0
}

export function RunPage({ runId, health, onOpenSettings, onChanged, onHome, onDeleted }: {
  runId: string
  health: Health | null
  onOpenSettings: () => void
  onChanged: () => void
  onHome: () => void
  onDeleted: () => void
}) {
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [plan, setPlan] = useState<Plan | null>(null)
  const [tab, setTab] = useState<Tab>('overview')
  const [aTab, setATab] = useState<AnalysisTab>('architecture')
  const [program, setProgram] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const lastEvent = useRef(0)
  const autoTab = useRef<string>('')

  const load = useCallback(async () => {
    try {
      const r = await api.run(runId)
      setRun(r)
      if (r.has_analysis) api.analysis(runId).then(setAnalysis).catch(() => undefined)
      if (r.has_plan) api.plan(runId).then(setPlan).catch(() => undefined)
      return r
    } catch (e) {
      setError((e as Error).message)
      return null
    }
  }, [runId])

  // live updates: server-sent events, with periodic refetch as the source of truth
  useEffect(() => {
    load()
    const es = new EventSource(`/api/runs/${runId}/stream`)
    es.addEventListener('log', (m) => {
      const ev = JSON.parse((m as MessageEvent).data) as RunEvent
      if (ev.id > lastEvent.current) {
        lastEvent.current = ev.id
        setEvents((prev) => [...prev, ev])
      }
    })
    let lastState = ''
    es.addEventListener('state', (m) => {
      const raw = (m as MessageEvent).data as string
      const st = JSON.parse(raw) as { status: string }
      if (raw !== lastState) { // the server repeats the state every second; only react to changes
        lastState = raw
        load()
        onChanged()
      }
      if (['completed', 'failed', 'rejected'].includes(st.status)) es.close() // do not auto-reconnect
    })
    const poll = setInterval(load, 3000)
    return () => { es.close(); clearInterval(poll) }
  }, [runId, load, onChanged])

  // when the run needs a human, take them to the one screen where they can act
  useEffect(() => {
    if (!run?.pending) return
    const key = `${run.pending.type}:${run.pending.program ?? ''}`
    if (autoTab.current === key) return
    autoTab.current = key
    if (run.pending.type === 'plan_approval') setTab('plan')
    else { setTab('results'); setProgram(run.pending.program ?? null) }
  }, [run?.pending])

  const decidePlan = async (approved: boolean, programs: string[]) => {
    try { await api.decidePlan(runId, approved, programs); setTab('overview'); await load(); onChanged() }
    catch (e) { setError((e as Error).message) }
  }
  const decideUnit = async (p: string, d: 'approve' | 'reject' | 'retry', force: boolean) => {
    try { await api.decideUnit(runId, p, d, force); await load(); onChanged() }
    catch (e) { setError((e as Error).message) }
  }

  const refreshKey = useMemo(() => JSON.stringify(Object.values(run?.units ?? {}).map((u) => u.decision?.commit)) + run?.status, [run])

  if (!run) {
    return <div className="container page-body"><div className="empty">{error ?? 'Loading run…'}</div></div>
  }
  const step = guideStep(run)
  const unitCount = Object.keys(run.units).length
  const hasOutput = run.has_report || Object.values(run.units).some((u) => u.decision?.decision === 'approve')

  const tabs: { id: Tab; label: string; icon: string; count?: number; show: boolean }[] = [
    { id: 'overview', label: 'Overview', icon: 'overview', show: true },
    { id: 'plan', label: 'Plan', icon: 'plan', show: !!plan },
    { id: 'results', label: 'Results', icon: 'migrate', show: unitCount > 0 || !!plan, count: unitCount || undefined },
    { id: 'output', label: 'Output files', icon: 'package', show: hasOutput },
    { id: 'analysis', label: 'Analysis', icon: 'graph', show: !!analysis },
  ]

  return (
    <>
      <div className="page-head">
        <div className="container">
          <div className="breadcrumb">
            <button className="crumb" onClick={onHome}>Migrations</button>
            <span className="sep">/</span>
            <b>{run.name}</b>
          </div>
          <div className="title-row">
            <h1>{run.name}<span className="num">#{run.id.slice(-6)}</span></h1>
            <RunState status={run.status} />
            <button className="btn sm danger" onClick={async () => { if (confirm('Delete this run and its output?')) { await api.deleteRun(runId); onDeleted() } }}>
              <Icon name="trash" /> Delete
            </button>
          </div>
          <div className="tabs" role="tablist" aria-label="Migration sections">
            {tabs.filter((t) => t.show).map((t) => (
              <button key={t.id} role="tab" aria-selected={tab === t.id} aria-current={tab === t.id ? 'page' : undefined}
                className={`tab ${tab === t.id ? 'active' : ''}`} onClick={() => setTab(t.id)}>
                <span className="tab-inner">
                  <Icon name={t.icon} />
                  {t.label}
                  {t.count !== undefined && <span className="counter">{t.count}</span>}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="container page-body">
        <div className="stack">
          <div className="guide" role="list" aria-label="Where you are">
            {GUIDE.map((g, i) => {
              const done = i < step
              const current = i === step
              const waiting = current && run.status === 'awaiting_approval'
              return (
                <div key={g.title} role="listitem" aria-current={current ? 'step' : undefined}
                  className={`gstep ${done ? 'done' : waiting ? 'wait' : current ? 'active' : ''}`}>
                  <span className="gnum">{done ? <Icon name="check" size={18} /> : i + 1}</span>
                  <span><b>{g.title}</b><span className="st">{done ? 'Done' : waiting ? 'Waiting for you' : current ? 'In progress' : g.desc}</span></span>
                </div>
              )
            })}
          </div>

          {error && <div className="notice bad" onClick={() => setError(null)}>{error}</div>}

          {tab === 'overview' && (
            <Overview run={run} events={events} analysis={analysis} health={health}
              onGo={setTab} onOpenSettings={onOpenSettings} />
          )}
          {tab === 'plan' && plan && <PlanView plan={plan} pending={run.pending} onDecide={decidePlan} />}
          {tab === 'results' && (
            <ResultsView run={run} health={health} focus={program} onFocus={setProgram} onDecide={decideUnit}
              onGoPlan={() => setTab('plan')} />
          )}
          {tab === 'output' && <OutputView runId={runId} refreshKey={refreshKey} />}
          {tab === 'analysis' && analysis && (
            <div className="stack">
              <div className="row">
                <div className="segmented" role="tablist" aria-label="Analysis sections">
                  {([['architecture', 'Architecture'], ['programs', 'Programs'], ['security', `Security (${analysis.security.findings.length})`]] as [AnalysisTab, string][]).map(([id, label]) => (
                    <button key={id} role="tab" aria-selected={aTab === id} className={aTab === id ? 'on' : ''} onClick={() => setATab(id)}>{label}</button>
                  ))}
                </div>
                <span className="muted small">What the platform learned about your code before changing anything.</span>
              </div>
              {aTab === 'architecture' && <ArchitectureView analysis={analysis} selected={program} onSelect={(k) => { setProgram(k); setATab('programs') }} />}
              {aTab === 'programs' && <ProgramsView runId={runId} analysis={analysis} selected={program} onSelect={setProgram} />}
              {aTab === 'security' && <SecurityView findings={analysis.security.findings} counts={analysis.security.counts} />}
            </div>
          )}
        </div>
      </div>
    </>
  )
}

/** The one thing the user should do next, in plain words. */
function NextAction({ run, lastMessage, onGo }: { run: Run; lastMessage: string; onGo: (t: Tab) => void }) {
  const units = Object.entries(run.units)
  const saved = units.filter(([, u]) => u.decision?.decision === 'approve').length

  if (run.status === 'failed') {
    return (
      <div className="notice bad" style={{ alignItems: 'center' }}>
        <Icon name="x" size={20} />
        <div style={{ flex: 1 }}><b>Something went wrong</b><div className="small">{run.error ?? 'The run stopped unexpectedly.'}</div></div>
      </div>
    )
  }
  if (run.status === 'rejected') {
    return <div className="notice info"><Icon name="x" size={20} /><div><b>Plan rejected</b><div className="small">Nothing was migrated. Start a new migration when you are ready.</div></div></div>
  }
  if (run.status === 'completed') {
    return (
      <div className="notice ok" style={{ alignItems: 'center' }}>
        <Icon name="check" size={20} />
        <div style={{ flex: 1 }}>
          <b>All done</b>
          <div className="small">{saved} of {units.length} program{units.length === 1 ? '' : 's'} saved to the output repository. See exactly what each produced, then take the migrated code.</div>
        </div>
        <div className="row">
          <button className="btn primary" onClick={() => onGo('results')}>See results</button>
          {saved > 0 && <button className="btn" onClick={() => onGo('output')}>Output files</button>}
          {saved > 0 && <a className="btn" href={api.archiveUrl(run.id)}><Icon name="download" /> Download .zip</a>}
        </div>
      </div>
    )
  }
  if (run.pending?.type === 'plan_approval') {
    return (
      <div className="approval">
        <span className="ap-ic"><Icon name="alert" size={22} /></span>
        <div style={{ flex: 1, minWidth: 240 }}>
          <b>Next: approve the migration plan</b>
          <div className="muted small">Nothing has been changed yet. Check which programs will be migrated, in what order, and the risks we found. Then approve to start.</div>
        </div>
        <button className="btn primary" onClick={() => onGo('plan')}>Review the plan</button>
      </div>
    )
  }
  if (run.pending?.type === 'unit_approval') {
    const p = run.pending.program!
    const u = run.units[p]
    return (
      <div className="approval">
        <span className="ap-ic"><Icon name="alert" size={22} /></span>
        <div style={{ flex: 1, minWidth: 240 }}>
          <b>Next: check the results for {p}</b>
          <div className="muted small">
            {u ? (u.status === 'verified'
              ? `Its output matches the original on all ${u.total} test case${u.total === 1 ? '' : 's'}. See the inputs and outputs, then approve to save it.`
              : 'It does not match the original yet. See what differs before deciding.') : run.pending.message}
          </div>
        </div>
        <button className="btn primary" onClick={() => onGo('results')}>See results</button>
      </div>
    )
  }
  return (
    <div className="notice info" style={{ alignItems: 'center' }}>
      <span className="badge info pulse" style={{ border: 0 }} />
      <div style={{ flex: 1 }}>
        <b>Working…</b>
        <div className="small muted">{lastMessage || 'Starting'}. You do not need to do anything yet - this page will tell you when it needs you.</div>
      </div>
    </div>
  )
}

function Overview({ run, events, analysis, health, onGo, onOpenSettings }: {
  run: Run
  events: RunEvent[]
  analysis: Analysis | null
  health: Health | null
  onGo: (t: Tab) => void
  onOpenSettings: () => void
}) {
  const units = Object.entries(run.units)
  const verified = units.filter(([, u]) => u.status === 'verified').length
  const last = events.length ? events[events.length - 1].message : ''
  return (
    <div className="stack">
      <NextAction run={run} lastMessage={last} onGo={onGo} />

      {analysis && (
        <div className="stack" style={{ gap: 8 }}>
          <h3>What we found in your code</h3>
          <div className="kpis">
            <div className="kpi"><div className="v">{analysis.project.programs}</div><div className="l">COBOL programs</div></div>
            <div className="kpi"><div className="v">{analysis.project.loc.toLocaleString()}</div><div className="l">Lines of code</div></div>
            <div className="kpi"><div className="v">{Object.values(analysis.programs).reduce((a, p) => a + p.rules.rules.length, 0)}</div><div className="l">Business rules</div></div>
            <div className="kpi"><div className="v">{analysis.security.findings.length}</div><div className="l">Risks to look at</div></div>
            {units.length > 0 && <div className="kpi"><div className="v">{verified}/{units.length}</div><div className="l">Programs matching the original</div></div>}
          </div>
          <div className="row">
            <button className="btn sm" onClick={() => onGo('analysis')}>Explore the analysis</button>
          </div>
        </div>
      )}

      {!health?.llm.configured && (
        <div className="notice info row">
          <span style={{ flex: 1 }}>No Groq API key is set. The run works without one, but programs that use statements the automatic translator does not know will need it.</span>
          <button className="btn sm" onClick={onOpenSettings}><Icon name="key" /> Add your API key</button>
        </div>
      )}

      <details className="card">
        <summary className="card-head" style={{ cursor: 'pointer' }}>
          <h3>Activity log</h3><span className="counter">{events.length}</span>
        </summary>
        <div className="log" role="log" aria-live="polite">
          {events.map((e) => (
            <div key={e.id} className={`log-line ${e.level}`}>
              <span className="t">{new Date(e.ts * 1000).toLocaleTimeString([], { hour12: false })}</span>
              <span className="s">{e.stage}</span>
              <span className="m">{e.message}</span>
            </div>
          ))}
          {events.length === 0 && <div className="log-line"><span /><span /><span className="muted">Waiting for events…</span></div>}
        </div>
      </details>
    </div>
  )
}
