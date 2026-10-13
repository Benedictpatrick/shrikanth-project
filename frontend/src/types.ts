export interface Pending {
  type: 'plan_approval' | 'unit_approval'
  message: string
  program?: string
  status?: string
  units?: string[]
}

export interface UnitSummary {
  status: UnitStatus
  oracle: Oracle
  decision: { decision: string; note?: string; commit?: string | null; auto?: boolean } | null
  passed: number
  total: number
  attempts: number
}

export type UnitStatus = 'verified' | 'provisional' | 'failed' | 'blocked' | 'unverified'
export type Oracle = 'cobc' | 'fixture' | 'llm-unverified' | 'none'

export interface Run {
  id: string
  name: string
  status: string
  stage: string
  created: number
  updated: number
  options: { auto_approve_verified?: boolean }
  error: string | null
  pending: Pending | null
  units: Record<string, UnitSummary>
  has_analysis: boolean
  has_plan: boolean
  has_report: boolean
}

export interface RunEvent {
  id: number
  run_id: string
  ts: number
  stage: string
  level: 'info' | 'warning' | 'error'
  message: string
  data: Record<string, unknown> | null
}

export interface LLMUsage {
  calls?: number
  prompt_tokens?: number
  completion_tokens?: number
  window_requests?: number
  window_tokens?: number
  rpm?: number
  tpm?: number
  throttled?: number
  waited_seconds?: number
  exhausted_seconds?: number
  exhausted_reason?: string
  daily_requests_remaining?: number | null
  last_error?: string
}

export interface Health {
  ok: boolean
  llm: { llm_provider: string; llm_configured: boolean; model: string; available: boolean; active_model: string; model_ok: boolean | null; model_detail: string; key_hint: string; configured: boolean; rpm: number; tpm: number; usage: LLMUsage }
  cobc: string | null
  git: boolean
}

export interface Sample {
  id: string
  path: string
  programs: string[]
}

export interface Metrics {
  loc: number
  paragraphs: number
  statements: number
  decisions: number
  complexity: number
  data_items: number
  files: number
  copybooks: number
  unsupported: number
  translatable: number
}

export interface Finding {
  program: string
  severity: 'high' | 'medium' | 'low'
  rule: string
  title: string
  detail: string
  file: string
  line: number
  recommendation: string
}

export interface Rule {
  id: string
  category: string
  title: string
  description: string
  paragraph: string
  file: string
  line: number
  origin: 'static' | 'llm'
}

export interface DataItemView {
  level: number
  name: string
  pic: string | null
  usage: string
  occurs: number | null
  redefines: string | null
  parent: string | null
  value: string | null
  section: string
  flags: string[]
}

export interface ProgramAnalysis {
  program_id: string
  path: string
  module: string
  metrics: Metrics
  security: Finding[]
  rules: { summary: string; rules: Rule[]; used_llm?: boolean }
  baseline: {
    oracle: Oracle
    notes: string[]
    cases: { name: string; description: string; source: string | null; inputs: string[]; expected_files: string[] }[]
  }
  doc: string
  model: {
    files: { name: string; assign: string; organization: string; status: string | null; records: string[] }[]
    data: DataItemView[]
    paragraphs: { name: string; section: string | null; line: number; end_line: number; statements: number }[]
    calls: string[]
    copybooks: string[]
    missing_copybooks: string[]
    unsupported: { line: number; file: string; construct: string; text: string; where: string }[]
    paragraph_calls: Record<string, string[]>
  }
}

export interface GraphNode {
  id: string
  type: 'program' | 'copybook' | 'file' | 'external'
  label: string
  path?: string
  complexity?: number
  loc?: number
  unsupported?: number
}

export interface GraphEdge {
  source: string
  target: string
  type: 'copy' | 'call' | 'file'
  mode?: 'read' | 'write' | 'readwrite'
}

export interface Analysis {
  project: { programs: number; copybooks: string[]; loc: number; errors: string[] }
  graph: { nodes: GraphNode[]; edges: GraphEdge[] }
  programs: Record<string, ProgramAnalysis>
  security: { findings: Finding[]; counts: { high: number; medium: number; low: number } }
  llm: { provider: string; model: string; available: boolean }
}

export interface PlanUnit {
  order: number
  program: string
  path: string
  module: string
  depends_on: string[]
  complexity: number
  loc: number
  translation: 'rules' | 'rules+llm'
  oracle: Oracle
  test_cases: number
  risks: string[]
  rationale: string
}

export interface Plan {
  strategy: string
  summary: string
  target: { language: string; runtime: string; layout: string }
  approach: string
  units: PlanUnit[]
  decisions: { id: string; question: string; choice: string; alternatives: string[] }[]
  global_risks: string[]
}

export interface CaseIO {
  stdout: string
  files: Record<string, string>
}

export interface CaseResult {
  name: string
  description: string
  inputs: Record<string, string>
  expected: CaseIO
  actual: CaseIO
  passed: boolean
  diff: string[]
  error: string | null
  traceback: string | null
  source: string
}

export interface Attempt {
  number: number
  source: string
  passed: number
  failed: number
  total: number
  note: string
  syntax_error: string | null
}

export interface Unit {
  program: string
  module: string
  status: UnitStatus
  oracle: Oracle
  issues: { line: number; message: string }[]
  notes: string[]
  attempts: Attempt[]
  verification: { total: number; passed: number; failed: number; results: CaseResult[] }
  code: string
  cobol: string
  decision: { decision: string; note?: string; commit?: string | null; auto?: boolean } | null
}

export interface OutputTree {
  files: { path: string; size: number }[]
  log: { hash: string; subject: string; date: string }[]
}
