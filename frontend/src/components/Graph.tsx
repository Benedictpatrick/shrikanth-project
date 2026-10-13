import { useMemo, useState } from 'react'
import type { GraphEdge, GraphNode } from '../types'

const W = 220
const H = 46
const GAP = 26
const COL = { copybook: 30, program: 380, file: 730, external: 380 }

export function Graph({
  nodes,
  edges,
  selected,
  onSelect,
}: {
  nodes: GraphNode[]
  edges: GraphEdge[]
  selected?: string | null
  onSelect?: (programKey: string) => void
}) {
  const [hover, setHover] = useState<string | null>(null)

  const layout = useMemo(() => {
    const cols: Record<string, GraphNode[]> = { copybook: [], program: [], file: [], external: [] }
    nodes.forEach((n) => cols[n.type].push(n))
    const pos = new Map<string, { x: number; y: number }>()
    const place = (arr: GraphNode[], x: number, y0 = 20) =>
      arr.forEach((n, i) => pos.set(n.id, { x, y: y0 + i * (H + GAP) }))
    place(cols.copybook, COL.copybook)
    place(cols.program, COL.program)
    place(cols.file, COL.file)
    const progBottom = 20 + cols.program.length * (H + GAP)
    place(cols.external, COL.external, progBottom + 10)
    const height = Math.max(
      ...[...pos.values()].map((p) => p.y + H + 20),
      160,
    )
    return { pos, height }
  }, [nodes])

  const touches = (e: GraphEdge, id: string | null) => !!id && (e.source === id || e.target === id)

  return (
    <div className="graph-wrap">
      <svg width={COL.file + W + 30} height={layout.height} role="img" aria-label="Dependency graph of programs, copybooks and files">
        <defs>
          <marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--line-strong)" />
          </marker>
        </defs>
        <text x={COL.copybook} y={12} className="sub" fill="var(--faint)" fontSize="11" style={{ letterSpacing: '.08em' }}>COPYBOOKS</text>
        <text x={COL.program} y={12} fill="var(--faint)" fontSize="11" style={{ letterSpacing: '.08em' }}>PROGRAMS</text>
        <text x={COL.file} y={12} fill="var(--faint)" fontSize="11" style={{ letterSpacing: '.08em' }}>FILES</text>
        {edges.map((e, i) => {
          const a = layout.pos.get(e.source)
          const b = layout.pos.get(e.target)
          if (!a || !b) return null
          let d: string
          if (e.type === 'copy') {
            // program (left edge) -> copybook (right edge)
            const x1 = a.x, y1 = a.y + H / 2, x2 = b.x + W, y2 = b.y + H / 2
            d = `M${x1},${y1} C${x1 - 70},${y1} ${x2 + 70},${y2} ${x2},${y2}`
          } else if (e.type === 'file') {
            const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2
            d = `M${x1},${y1} C${x1 + 70},${y1} ${x2 - 70},${y2} ${x2},${y2}`
          } else {
            const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x + W, y2 = b.y + H / 2
            d = `M${x1},${y1} C${x1 + 60},${y1} ${x2 + 60},${y2} ${x2},${y2}`
          }
          const lit = touches(e, hover)
          const dim = hover && !lit
          return (
            <path
              key={i}
              d={d}
              className={`g-edge ${e.type === 'call' ? 'call' : ''} ${lit ? 'lit' : ''} ${dim ? 'dim' : ''}`}
              markerEnd="url(#arr)"
            />
          )
        })}
        {nodes.map((n) => {
          const p = layout.pos.get(n.id)!
          const key = n.id.split(':').slice(1).join(':')
          const sub =
            n.type === 'program'
              ? `${n.loc} lines · complexity ${n.complexity}${n.unsupported ? ` · ${n.unsupported} unsupported` : ''}`
              : n.type === 'copybook'
                ? 'copybook'
                : n.type === 'file'
                  ? 'data file'
                  : 'external program'
          return (
            <g
              key={n.id}
              className={`g-node ${selected === key ? 'sel' : ''}`}
              transform={`translate(${p.x},${p.y})`}
              onMouseEnter={() => setHover(n.id)}
              onMouseLeave={() => setHover(null)}
              onClick={() => n.type === 'program' && onSelect?.(key)}
              tabIndex={n.type === 'program' ? 0 : undefined}
              onKeyDown={(ev) => ev.key === 'Enter' && n.type === 'program' && onSelect?.(key)}
            >
              <rect width={W} height={H} rx={9} />
              <rect width={4} height={H} rx={2} style={{ fill: n.type === 'program' ? 'var(--accent)' : n.type === 'file' ? 'var(--info)' : 'var(--faint)', stroke: 'none' }} />
              <text x={16} y={20} fontWeight={600}>{n.label}</text>
              <text x={16} y={36} className="sub">{sub}</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
