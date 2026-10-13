import type { Oracle, UnitStatus } from '../types'
import { Icon } from './icons'

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, [string, string, boolean]> = {
    created: ['neutral', 'Created', false],
    running: ['info', 'Running', true],
    awaiting_approval: ['warn', 'Needs your approval', true],
    completed: ['ok', 'Completed', false],
    failed: ['bad', 'Failed', false],
    rejected: ['neutral', 'Rejected', false],
  }
  const [cls, label, pulse] = map[status] ?? ['neutral', status, false]
  return <span className={`badge ${cls}${pulse ? ' pulse' : ''}`}>{label}</span>
}

const RUN_STATE: Record<string, { cls: string; icon: string; label: string }> = {
  created: { cls: 'draft', icon: 'pending', label: 'Created' },
  running: { cls: 'progress', icon: 'progress', label: 'In progress' },
  awaiting_approval: { cls: 'attention', icon: 'alert', label: 'Review required' },
  completed: { cls: 'merged', icon: 'merge', label: 'Completed' },
  failed: { cls: 'closed', icon: 'x', label: 'Failed' },
  rejected: { cls: 'draft', icon: 'x', label: 'Rejected' },
}

export function runState(status: string) {
  return RUN_STATE[status] ?? RUN_STATE.created
}

/** Pull-request style state label: "Completed" is purple like Merged, "Review required" is amber. */
export function RunState({ status }: { status: string }) {
  const s = runState(status)
  return (
    <span className={`state ${s.cls}`}>
      <Icon name={s.icon} />
      {s.label}
    </span>
  )
}

export function UnitBadge({ status }: { status: UnitStatus | string }) {
  const map: Record<string, [string, string]> = {
    verified: ['ok', 'Matches original'],
    provisional: ['warn', 'Unconfirmed match'],
    unverified: ['warn', 'Not compared'],
    failed: ['bad', 'Differs from original'],
    blocked: ['bad', 'Needs attention'],
  }
  const [cls, label] = map[status] ?? ['neutral', status]
  return <span className={`badge ${cls}`}>{label}</span>
}

export const ORACLE_PLAIN: Record<Oracle, string> = {
  cobc: 'the original COBOL program, actually run with GnuCOBOL',
  fixture: 'test cases that came with your repository',
  'llm-unverified': 'expected results predicted by AI (not confirmed by the original program)',
  none: 'nothing - no test data is available',
}

export const ORACLE_LABEL: Record<Oracle, string> = {
  cobc: 'Original COBOL run with GnuCOBOL',
  fixture: 'Test cases from your repo',
  'llm-unverified': 'AI-predicted (unconfirmed)',
  none: 'No test data',
}

export function OracleBadge({ oracle }: { oracle: Oracle }) {
  const cls = oracle === 'cobc' ? 'ok' : oracle === 'fixture' ? 'info' : oracle === 'llm-unverified' ? 'warn' : 'bad'
  return (
    <span className={`badge ${cls}`} title={ORACLE_LABEL[oracle]}>
      {ORACLE_LABEL[oracle]}
    </span>
  )
}

export function SeverityBadge({ severity }: { severity: string }) {
  const cls = severity === 'high' ? 'bad' : severity === 'medium' ? 'warn' : 'neutral'
  return <span className={`badge ${cls}`}>{severity}</span>
}

export function relTime(ts: number): string {
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}

export function Kpi({ value, label }: { value: React.ReactNode; label: string }) {
  return (
    <div className="kpi">
      <div className="v">{value}</div>
      <div className="l">{label}</div>
    </div>
  )
}
