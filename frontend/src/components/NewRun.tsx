import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Health, Run, Sample } from '../types'

export function NewRun({ health, onCreated, onOpenSettings }: { health: Health | null; onCreated: (r: Run) => void; onOpenSettings: () => void }) {
  const [samples, setSamples] = useState<Sample[]>([])
  const [choice, setChoice] = useState<{ kind: 'sample' | 'upload' | 'path'; value: string }>({ kind: 'sample', value: '' })
  const [auto, setAuto] = useState(false)
  const [path, setPath] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.samples().then((s) => {
      setSamples(s)
      if (s.length) setChoice({ kind: 'sample', value: s[0].id })
    }).catch(() => undefined)
  }, [])

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      let run: Run
      if (choice.kind === 'sample') run = await api.createRun({ sample: choice.value, auto_approve_verified: auto })
      else if (choice.kind === 'path') run = await api.createRun({ path, auto_approve_verified: auto })
      else run = await api.upload(file!, auto)
      onCreated(run)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const ready =
    (choice.kind === 'sample' && !!choice.value) || (choice.kind === 'path' && path.trim() !== '') || (choice.kind === 'upload' && !!file)

  return (
    <div className="stack">
      <div className="principles">
        <div className="principle"><b>Understand before transforming</b><span className="muted small">AST parsing, dependency graph, business rules and a security scan come first.</span></div>
        <div className="principle"><b>Verify before accepting</b><span className="muted small">Every unit runs against a behavioural baseline; failures are repaired, never waved through.</span></div>
        <div className="principle"><b>Preserve business behaviour</b><span className="muted small">COBOL numeric semantics live in a tested runtime, not in prompts.</span></div>
      </div>

      <div className="card card-pad stack">
        <h2>1 · Choose the repository</h2>
        <div className="stack" role="radiogroup" aria-label="Repository source">
          {samples.map((s) => (
            <label key={s.id} className={`choice ${choice.kind === 'sample' && choice.value === s.id ? 'sel' : ''}`}>
              <input type="radio" name="src" checked={choice.kind === 'sample' && choice.value === s.id} onChange={() => setChoice({ kind: 'sample', value: s.id })} />
              <div>
                <b>Bundled sample: {s.id}</b>
                <div className="muted small">{s.programs.length} COBOL programs ({s.programs.join(', ')}) with golden test fixtures. Includes one program that needs the LLM path.</div>
              </div>
            </label>
          ))}
          <label className={`choice ${choice.kind === 'upload' ? 'sel' : ''}`}>
            <input type="radio" name="src" checked={choice.kind === 'upload'} onChange={() => setChoice({ kind: 'upload', value: '' })} />
            <div style={{ display: 'grid', gap: 8 }}>
              <b>Upload a .zip of your repository</b>
              <div className="row">
                <input ref={fileRef} type="file" accept=".zip" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setChoice({ kind: 'upload', value: '' }) }} />
              </div>
              <span className="muted small">.cbl / .cob / .cobol programs and .cpy copybooks. Optional <code>tests/&lt;PROGRAM&gt;.json</code> golden cases are used as the behavioural baseline.</span>
            </div>
          </label>
          <label className={`choice ${choice.kind === 'path' ? 'sel' : ''}`}>
            <input type="radio" name="src" checked={choice.kind === 'path'} onChange={() => setChoice({ kind: 'path', value: '' })} />
            <div style={{ display: 'grid', gap: 8 }}>
              <b>Local folder on the machine running the server</b>
              <input type="text" placeholder="C:\legacy\payments" value={path} onFocus={() => setChoice({ kind: 'path', value: '' })} onChange={(e) => setPath(e.target.value)} />
            </div>
          </label>
        </div>

        <h2>2 · Approvals</h2>
        <label className="checkbox">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          <span>
            Auto-approve units that pass a <b>trusted</b> baseline
            <span className="muted small" style={{ display: 'block' }}>
              Off by default. The plan always needs your approval; units verified against fixtures or real COBOL output skip the per-unit review. Anything unverified or failing still stops for a human.
            </span>
          </span>
        </label>

        {error && <div className="notice bad">{error}</div>}
        <div className="row">
          <button className="btn primary" disabled={!ready || busy} onClick={start}>{busy ? 'Starting…' : 'Start analysis'}</button>
          <span className="muted small">Nothing is written to your repository; results go to a separate output repo with one commit per unit.</span>
        </div>
      </div>

      <div className="card card-pad row">
        <div style={{ flex: 1, minWidth: 240 }}>
          <b>Language model (Groq)</b>
          <div className="muted small">
            {health?.llm.configured
              ? `Your key ${health.llm.key_hint} · model ${health.llm.model} · limits ${health.llm.rpm} req/min, ${health.llm.tpm.toLocaleString()} tokens/min${health.llm.available ? '' : ' · quota reached, running without the LLM for now'}`
              : 'No API key set. Analysis and rule-based translation work without one; add your own Groq key to enable rule interpretation, docs, planning rationale and LLM repair.'}
          </div>
        </div>
        <button className="btn" onClick={onOpenSettings}>{health?.llm.configured ? 'Key, model & limits' : 'Add your API key'}</button>
      </div>
    </div>
  )
}
