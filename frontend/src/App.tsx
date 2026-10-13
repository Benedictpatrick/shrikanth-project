import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { Health, Run } from './types'
import { Icon } from './components/icons'
import { NewRun } from './components/NewRun'
import { RunList } from './components/RunList'
import { RunPage } from './components/RunPage'
import { SettingsDialog } from './components/SettingsDialog'

type Theme = 'system' | 'light' | 'dark'
type View = { name: 'runs' } | { name: 'new' } | { name: 'run'; id: string }

function loadTheme(): Theme {
  try {
    const t = localStorage.getItem('lca-theme')
    return t === 'light' || t === 'dark' ? t : 'system'
  } catch {
    return 'system'
  }
}

function viewFromHash(): View {
  const h = window.location.hash.replace(/^#\/?/, '')
  if (h === 'new') return { name: 'new' }
  const m = /^runs\/(.+)$/.exec(h)
  return m ? { name: 'run', id: m[1] } : { name: 'runs' }
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [view, setView] = useState<View>(viewFromHash)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [theme, setTheme] = useState<Theme>(loadTheme)

  const refreshHealth = useCallback(() => api.health().then(setHealth).catch(() => setHealth(null)), [])
  const refreshRuns = useCallback(() => api.runs().then(setRuns).catch(() => undefined), [])

  useEffect(() => {
    refreshHealth()
    refreshRuns()
    const t = setInterval(refreshRuns, 4000)
    return () => clearInterval(t)
  }, [refreshHealth, refreshRuns])

  // hash routing so refresh / back / forward behave like a normal site
  useEffect(() => {
    const on = () => setView(viewFromHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  const go = (v: View) => {
    window.location.hash = v.name === 'runs' ? '/' : v.name === 'new' ? '/new' : `/runs/${v.id}`
  }

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try { localStorage.setItem('lca-theme', theme) } catch { /* private mode */ }
  }, [theme])

  const attention = runs.filter((r) => r.status === 'awaiting_approval').length

  return (
    <>
      <header className="topbar">
        <button className="brand" onClick={() => go({ name: 'runs' })} aria-label="Legacy Code Automation home">
          <span className="brand-mark"><Icon name="logo" size={32} /></span>
          Legacy Code Automation
        </button>
        <span className="tagline"><b>Understand</b> before transforming · <b>Verify</b> before accepting · <b>Preserve</b> behaviour</span>
        <span className="spacer" />
        <div className="header-actions">
          {health ? (
            <>
              <button
                className="btn sm header"
                onClick={() => setSettingsOpen(true)}
                title={health.llm.model_detail || health.llm.usage?.exhausted_reason || 'Language model settings'}
              >
                <span style={{ color: health.llm.available && health.llm.model_ok !== false ? 'var(--ok)' : 'var(--warn)' }}>
                  <Icon name={health.llm.available && health.llm.model_ok !== false ? 'check' : 'alert'} />
                </span>
                {!health.llm.configured ? 'LLM off' : !health.llm.available ? 'Groq · quota reached' : health.llm.model_ok === false ? 'Groq · model problem' : `Groq · ${health.llm.active_model}`}
              </button>
              <span className={`badge ${health.cobc ? 'ok' : ''}`} title="GnuCOBOL compiler used as a behavioural oracle">{health.cobc ? 'cobc found' : 'no cobc'}</span>
              <span className={`badge ${health.git ? 'ok' : 'warn'}`}>{health.git ? 'git' : 'no git'}</span>
            </>
          ) : (
            <span className="badge bad">API offline</span>
          )}
          <button className="btn sm header" onClick={() => setSettingsOpen(true)} aria-haspopup="dialog">
            <Icon name="gear" /> Settings
          </button>
        </div>
      </header>
      <SettingsDialog open={settingsOpen} health={health} theme={theme} onTheme={setTheme} onClose={() => setSettingsOpen(false)} onHealth={refreshHealth} />

      {view.name === 'run' ? (
        <RunPage key={view.id} runId={view.id} health={health} onOpenSettings={() => setSettingsOpen(true)}
          onChanged={refreshRuns} onHome={() => go({ name: 'runs' })}
          onDeleted={() => { refreshRuns(); go({ name: 'runs' }) }} />
      ) : (
        <>
          <div className="page-head">
            <div className="container">
              {view.name === 'new' && (
                <div className="breadcrumb">
                  <button className="crumb" onClick={() => go({ name: 'runs' })}>Migrations</button>
                  <span className="sep">/</span><b>New</b>
                </div>
              )}
              <div className="title-row">
                <h1>{view.name === 'new' ? 'New migration' : 'Migrations'}</h1>
                {view.name === 'runs' && runs.length > 0 && (
                  <button className="btn primary" onClick={() => go({ name: 'new' })}><Icon name="plus" /> New migration</button>
                )}
              </div>
              <div className="tabs" role="tablist" aria-label="Sections">
                <button role="tab" aria-selected={view.name === 'runs'} className={`tab ${view.name === 'runs' ? 'active' : ''}`} onClick={() => go({ name: 'runs' })}>
                  <span className="tab-inner"><Icon name="migrate" /> Migrations <span className="counter">{runs.length}</span>
                    {attention > 0 && <span className="badge warn">{attention} to review</span>}</span>
                </button>
                <button role="tab" aria-selected={view.name === 'new'} className={`tab ${view.name === 'new' ? 'active' : ''}`} onClick={() => go({ name: 'new' })}>
                  <span className="tab-inner"><Icon name="plus" /> New migration</span>
                </button>
              </div>
            </div>
          </div>
          <div className="container page-body">
            {view.name === 'new' ? (
              <NewRun health={health} onOpenSettings={() => setSettingsOpen(true)}
                onCreated={(r) => { refreshRuns(); go({ name: 'run', id: r.id }) }} />
            ) : (
              <RunList runs={runs} onOpen={(id) => go({ name: 'run', id })} onNew={() => go({ name: 'new' })} />
            )}
          </div>
        </>
      )}
    </>
  )
}
