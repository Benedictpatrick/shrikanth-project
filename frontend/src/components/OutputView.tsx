import { useEffect, useState } from 'react'
import { api } from '../api'
import type { OutputTree } from '../types'
import { Code } from './Code'
import { Markdown } from './Markdown'

export function OutputView({ runId, refreshKey }: { runId: string; refreshKey: string }) {
  const [tree, setTree] = useState<OutputTree | null>(null)
  const [path, setPath] = useState<string | null>(null)
  const [content, setContent] = useState('')

  useEffect(() => {
    api.tree(runId).then((t) => {
      setTree(t)
      setPath((p) => p ?? t.files.find((f) => f.path.endsWith('.py') && !f.path.startsWith('tests') && f.path !== 'cobol_runtime.py')?.path ?? t.files[0]?.path ?? null)
    })
  }, [runId, refreshKey])

  useEffect(() => {
    if (path) api.file(runId, path).then(setContent).catch((e) => setContent(`# ${e.message}`))
  }, [runId, path, refreshKey])

  if (!tree) return <div className="empty">Loading…</div>
  const isMd = path?.endsWith('.md')
  const lang = path?.endsWith('.py') ? 'py' : path?.endsWith('.cbl') || path?.endsWith('.cpy') ? 'cobol' : 'plain'
  const sorted = [...tree.files].sort((a, b) => {
    const rank = (p: string) => (p.startsWith('legacy/') ? 3 : p.startsWith('tests/') ? 2 : p.startsWith('docs/') ? 1 : 0)
    return rank(a.path) - rank(b.path) || a.path.localeCompare(b.path)
  })

  return (
    <div className="stack">
      <div className="row">
        <a className="btn" href={api.archiveUrl(runId)}>Download migrated repository (.zip)</a>
        <span className="muted small">{tree.files.length} files · {tree.log.length} commit(s)</span>
      </div>
      <div className="grid-2" style={{ gridTemplateColumns: 'minmax(260px, 320px) minmax(0, 1fr)' }}>
        <div className="stack">
          <div className="card">
            <div className="card-head"><h3>Files</h3></div>
            <div className="card-pad file-list" style={{ padding: 8, maxHeight: 360, overflow: 'auto' }}>
              {sorted.map((f) => (
                <button key={f.path} className={`file-item ${f.path === path ? 'active' : ''}`} onClick={() => setPath(f.path)}>
                  <span>{f.path}</span><span className="faint">{f.size > 1024 ? `${(f.size / 1024).toFixed(1)}k` : f.size}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="card">
            <div className="card-head"><h3>Git history</h3></div>
            <div className="card-pad stack" style={{ gap: 8 }}>
              {tree.log.map((c) => (
                <div key={c.hash}><code>{c.hash}</code> {c.subject}<div className="faint small">{c.date}</div></div>
              ))}
              {!tree.log.length && <span className="muted">No commits yet.</span>}
            </div>
          </div>
        </div>
        <div>
          {path && (isMd
            ? <div className="card card-pad"><Markdown text={content} /></div>
            : <Code text={content} lang={lang} maxHeight={680} />)}
        </div>
      </div>
    </div>
  )
}
