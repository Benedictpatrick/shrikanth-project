import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Health } from '../types'

function Meter({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = Math.min(100, Math.round((value / Math.max(max, 1)) * 100))
  const tone = pct >= 90 ? 'var(--bad)' : pct >= 65 ? 'var(--warn)' : 'var(--ok)'
  return (
    <div className="meter">
      <div className="row" style={{ justifyContent: 'space-between', gap: 8 }}>
        <span className="small muted">{label}</span>
        <span className="small mono">{value.toLocaleString()} / {max.toLocaleString()}</span>
      </div>
      <div className="meter-track" role="progressbar" aria-label={label} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="meter-fill" style={{ width: `${pct}%`, background: tone }} />
      </div>
    </div>
  )
}

export function SettingsDialog({ open, health, theme, onTheme, onClose, onHealth }: {
  open: boolean
  health: Health | null
  theme: 'system' | 'light' | 'dark'
  onTheme: (t: 'system' | 'light' | 'dark') => void
  onClose: () => void
  onHealth: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const llm = health?.llm
  const [key, setKey] = useState('')
  const [reveal, setReveal] = useState(false)
  const [model, setModel] = useState('')
  const [rpm, setRpm] = useState('')
  const [tpm, setTpm] = useState('')
  const [models, setModels] = useState<string[]>([])
  const [remember, setRemember] = useState(false)
  const [busy, setBusy] = useState<'test' | 'save' | 'remove' | 'quota' | null>(null)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'bad' | 'info'; text: string } | null>(null)

  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) {
      setKey('')
      setReveal(false)
      setMsg(null)
      setModel(llm?.model ?? '')
      setRpm(String(llm?.rpm ?? 25))
      setTpm(String(llm?.tpm ?? 12000))
      d.showModal()
    } else if (!open && d.open) d.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // live usage while the dialog is open
  useEffect(() => {
    if (!open) return
    const t = setInterval(onHealth, 3000)
    return () => clearInterval(t)
  }, [open, onHealth])

  const limits = () => ({ rpm: Number(rpm) || undefined, tpm: Number(tpm) || undefined })

  const test = async () => {
    setBusy('test')
    setMsg(null)
    try {
      const r = await api.testLLM(key.trim() || null, model || undefined)
      setModels(r.models)
      if (!r.model_available && r.models.length) {
        const pick = r.models.find((m) => m.includes('70b')) ?? r.models[0]
        setMsg({ kind: 'info', text: `Key works. "${model}" is not available for it, so I selected "${pick}".` })
        setModel(pick)
      } else {
        setMsg({ kind: 'ok', text: `Key works. ${r.models.length} models available.` })
      }
    } catch (e) {
      setMsg({ kind: 'bad', text: (e as Error).message })
    } finally {
      setBusy(null)
    }
  }

  const save = async () => {
    setBusy('save')
    setMsg(null)
    try {
      const r = await api.setLLM(key.trim() || null, model || undefined, remember, limits())
      setKey('')
      onHealth()
      setMsg({ kind: 'ok', text: r.persisted ? 'Saved, and written to the .env file on this machine.' : 'Applied for this server session only.' })
    } catch (e) {
      setMsg({ kind: 'bad', text: (e as Error).message })
    } finally {
      setBusy(null)
    }
  }

  const remove = async () => {
    setBusy('remove')
    try {
      await api.setLLM('', undefined, remember)
      setKey('')
      onHealth()
      setMsg({ kind: 'info', text: 'API key removed. The platform will run without the LLM.' })
    } catch (e) {
      setMsg({ kind: 'bad', text: (e as Error).message })
    } finally {
      setBusy(null)
    }
  }

  const retryNow = async () => {
    setBusy('quota')
    try { await api.resetQuota(); onHealth() } finally { setBusy(null) }
  }

  const configured = !!llm?.configured
  const exhausted = (llm?.usage?.exhausted_seconds ?? 0) > 0
  const u = llm?.usage ?? {}
  const unchanged = !key.trim() && model === (llm?.model ?? '') && Number(rpm) === llm?.rpm && Number(tpm) === llm?.tpm
  const chip = !configured ? ['warn', 'No key'] : exhausted ? ['bad', 'Quota reached'] : llm?.model_ok === false ? ['warn', 'Model problem'] : ['ok', `Connected ${llm?.key_hint ?? ''}`]

  return (
    <dialog ref={ref} className="dialog" onClose={onClose} onClick={(e) => e.target === ref.current && onClose()}>
      <div className="dialog-body" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ marginBottom: 14 }}>
          <h2 style={{ flex: 1 }}>Settings</h2>
          <button className="btn sm ghost" onClick={onClose} aria-label="Close settings">Close</button>
        </div>

        <section className="stack">
          <div className="row">
            <h3 style={{ flex: 1 }}>Appearance</h3>
            <div className="segmented" role="radiogroup" aria-label="Theme">
              {(['system', 'light', 'dark'] as const).map((t) => (
                <button key={t} role="radio" aria-checked={theme === t} className={theme === t ? 'on' : ''} onClick={() => onTheme(t)}>
                  {t === 'system' ? 'System' : t === 'light' ? 'Light' : 'Dark'}
                </button>
              ))}
            </div>
          </div>
          <hr style={{ border: 0, borderTop: '1px solid var(--line)', margin: 0 }} />
          <div className="row">
            <h3 style={{ flex: 1 }}>Language model · Groq</h3>
            <span className={`badge ${chip[0]}`}>{chip[1]}</span>
          </div>
          <p className="muted small">
            Use your own Groq API key. It powers business-rule interpretation, documentation, planning rationale and the
            repair of constructs the rule-based translator cannot handle. Without a key everything else still works.
            Get a key at <a href="https://console.groq.com/keys" target="_blank" rel="noreferrer">console.groq.com/keys</a>.
          </p>
          {llm?.model_ok === false && llm.model_detail && <div className="notice warn small">{llm.model_detail}</div>}

          <label className="field">Your Groq API key
            <div className="row" style={{ gap: 8, flexWrap: 'nowrap' }}>
              <input
                type={reveal ? 'text' : 'password'}
                autoComplete="off"
                spellCheck={false}
                placeholder={configured ? `Current key ${llm?.key_hint ?? ''} — paste a new one to replace it` : 'gsk_…'}
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
              <button className="btn sm" type="button" onClick={() => setReveal((r) => !r)}>{reveal ? 'Hide' : 'Show'}</button>
            </div>
          </label>

          <label className="field">Model
            {models.length ? (
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                {!models.includes(model) && <option value={model}>{model} (not in list)</option>}
                {models.map((m) => <option key={m}>{m}</option>)}
              </select>
            ) : (
              <input type="text" value={model} onChange={(e) => setModel(e.target.value)} />
            )}
          </label>

          <div className="stack" style={{ gap: 10 }}>
            <div className="eyebrow">Rate limits</div>
            <p className="muted small">
              Groq limits requests and tokens per minute (and per day) and the numbers depend on your plan and model — see
              {' '}<a href="https://console.groq.com/settings/limits" target="_blank" rel="noreferrer">your limits page</a>.
              The platform throttles itself below these budgets, sizes prompts to fit, obeys Groq's <code>retry-after</code> hints,
              and if the daily quota is gone it stops calling and continues with static analysis and the rule-based translator.
            </p>
            <div className="grid-2">
              <label className="field">Requests per minute
                <input type="text" inputMode="numeric" value={rpm} onChange={(e) => setRpm(e.target.value.replace(/\D/g, ''))} />
              </label>
              <label className="field">Tokens per minute
                <input type="text" inputMode="numeric" value={tpm} onChange={(e) => setTpm(e.target.value.replace(/\D/g, ''))} />
              </label>
            </div>
            {configured && (
              <div className="card card-pad stack" style={{ gap: 10, background: 'var(--panel-2)' }}>
                <Meter label="Requests, last 60 s" value={u.window_requests ?? 0} max={llm?.rpm ?? 25} />
                <Meter label="Tokens, last 60 s" value={u.window_tokens ?? 0} max={llm?.tpm ?? 12000} />
                <div className="small muted">
                  {u.calls ?? 0} call(s) this session · {((u.prompt_tokens ?? 0) + (u.completion_tokens ?? 0)).toLocaleString()} tokens ·
                  throttled {u.throttled ?? 0}× ({u.waited_seconds ?? 0}s waiting)
                  {u.daily_requests_remaining != null && <> · {u.daily_requests_remaining.toLocaleString()} requests left today</>}
                </div>
                {exhausted && (
                  <div className="notice bad small row">
                    <span style={{ flex: 1 }}>
                      {u.exhausted_reason || 'Quota reached'}. LLM calls are paused for about {Math.ceil((u.exhausted_seconds ?? 0) / 60)} min;
                      runs continue without the LLM.
                    </span>
                    <button className="btn sm" onClick={retryNow} disabled={busy !== null}>Retry now</button>
                  </div>
                )}
                {!exhausted && u.last_error && <div className="faint small">Last error: {u.last_error.slice(0, 160)}</div>}
              </div>
            )}
          </div>

          <label className="checkbox">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            <span>
              Remember on this machine
              <span className="muted small" style={{ display: 'block' }}>
                Writes the key, model and limits to the <code>.env</code> file next to the project (git-ignored, stored in plain text).
                Off: they live in server memory only and are gone when the server stops.
              </span>
            </span>
          </label>

          {msg && <div className={`notice ${msg.kind} small`} role="status">{msg.text}</div>}

          <div className="row">
            <button className="btn" onClick={test} disabled={busy !== null || (!key.trim() && !configured)}>
              {busy === 'test' ? 'Testing…' : 'Test connection'}
            </button>
            <button className="btn primary" onClick={save} disabled={busy !== null || unchanged}>
              {busy === 'save' ? 'Saving…' : 'Save'}
            </button>
            <span className="spacer" />
            {configured && <button className="btn danger" onClick={remove} disabled={busy !== null}>Remove key</button>}
          </div>
          <p className="faint small">
            The key is sent only to Groq's API, never shown again after saving, and never included in run output.
            When it is set, the source of the program being processed and its failing test diffs are sent to Groq.
          </p>
        </section>
      </div>
    </dialog>
  )
}
