import type { ReactNode } from 'react'

function inline(text: string, key: string): ReactNode[] {
  const parts: ReactNode[] = []
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(_[^_]+_)/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('`')) parts.push(<code key={`${key}-${i++}`}>{tok.slice(1, -1)}</code>)
    else if (tok.startsWith('**')) parts.push(<strong key={`${key}-${i++}`}>{tok.slice(2, -2)}</strong>)
    else parts.push(<em key={`${key}-${i++}`}>{tok.slice(1, -1)}</em>)
    last = m.index + tok.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

/** Minimal Markdown renderer for the generated documentation (headings, tables, lists, quotes). */
export function Markdown({ text }: { text: string }) {
  const lines = text.split('\n')
  const out: ReactNode[] = []
  let i = 0
  let k = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) {
      i++
      continue
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    if (h) {
      const level = h[1].length
      const Tag = (`h${Math.min(level + 1, 4)}`) as 'h2' | 'h3' | 'h4'
      out.push(
        <Tag key={k++} style={{ margin: level === 1 ? '0 0 10px' : '20px 0 8px' }}>
          {inline(h[2], `h${k}`)}
        </Tag>,
      )
      i++
      continue
    }
    if (line.startsWith('|')) {
      const rows: string[][] = []
      while (i < lines.length && lines[i].startsWith('|')) {
        const cells = lines[i].split('|').slice(1, -1).map((c) => c.trim())
        if (!cells.every((c) => /^-+$/.test(c))) rows.push(cells)
        i++
      }
      const [head, ...body] = rows
      out.push(
        <div className="table-wrap" key={k++}>
          <table>
            <thead>
              <tr>{head.map((c, j) => <th key={j}>{inline(c, `th${k}${j}`)}</th>)}</tr>
            </thead>
            <tbody>
              {body.map((r, ri) => (
                <tr key={ri}>{r.map((c, j) => <td key={j}>{inline(c, `td${k}${ri}${j}`)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }
    if (/^\s*-\s+/.test(line)) {
      const items: { depth: number; text: string }[] = []
      while (i < lines.length && /^\s*-\s+/.test(lines[i])) {
        const m = /^(\s*)-\s+(.*)$/.exec(lines[i])!
        items.push({ depth: Math.floor(m[1].length / 2), text: m[2] })
        i++
      }
      out.push(
        <ul key={k++} style={{ margin: '6px 0', paddingLeft: 20 }}>
          {items.map((it, j) => (
            <li key={j} style={{ marginLeft: it.depth * 18 }}>
              {inline(it.text, `li${k}${j}`)}
            </li>
          ))}
        </ul>,
      )
      continue
    }
    if (line.startsWith('>')) {
      const q: string[] = []
      while (i < lines.length && lines[i].startsWith('>')) q.push(lines[i++].replace(/^>\s?/, ''))
      out.push(
        <p key={k++} className="muted small" style={{ margin: '4px 0' }}>
          {inline(q.join(' '), `q${k}`)}
        </p>,
      )
      continue
    }
    const para: string[] = []
    while (i < lines.length && lines[i].trim() && !/^(#|\||>|\s*-\s)/.test(lines[i])) para.push(lines[i++])
    out.push(
      <p key={k++} style={{ margin: '6px 0' }}>
        {inline(para.join(' '), `p${k}`)}
      </p>,
    )
  }
  return <div>{out}</div>
}
