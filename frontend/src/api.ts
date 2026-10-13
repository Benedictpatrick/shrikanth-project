import type { Analysis, Health, OutputTree, Plan, Run, RunEvent, Sample, Unit } from './types'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch('/api' + path, init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? JSON.stringify(body)
    } catch {
      /* not json */
    }
    throw new Error(detail)
  }
  const type = res.headers.get('content-type') ?? ''
  return (type.includes('json') ? res.json() : res.text()) as Promise<T>
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  health: () => req<Health>('/health'),
  samples: () => req<Sample[]>('/samples'),
  runs: () => req<Run[]>('/runs'),
  run: (id: string) => req<Run>(`/runs/${id}`),
  analysis: (id: string) => req<Analysis>(`/runs/${id}/analysis`),
  plan: (id: string) => req<Plan>(`/runs/${id}/plan`),
  unit: (id: string, program: string) => req<Unit>(`/runs/${id}/units/${program}`),
  source: (id: string, path: string) => req<string>(`/runs/${id}/source/${path}`),
  events: (id: string, after = 0) => req<RunEvent[]>(`/runs/${id}/events?after=${after}`),
  tree: (id: string) => req<OutputTree>(`/runs/${id}/output/tree`),
  file: (id: string, path: string) => req<string>(`/runs/${id}/output/file?path=${encodeURIComponent(path)}`),
  archiveUrl: (id: string) => `/api/runs/${id}/output/archive`,
  createRun: (body: { sample?: string; path?: string; auto_approve_verified: boolean }) =>
    req<Run>('/runs', json(body)),
  upload: (file: File, auto: boolean) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('auto_approve_verified', String(auto))
    return req<Run>('/runs/upload', { method: 'POST', body: fd })
  },
  deleteRun: (id: string) => req<{ deleted: string }>(`/runs/${id}`, { method: 'DELETE' }),
  decidePlan: (id: string, approved: boolean, programs: string[], note = '') =>
    req<Run>(`/runs/${id}/plan/decision`, json({ approved, programs, note })),
  decideUnit: (id: string, program: string, decision: 'approve' | 'reject' | 'retry', force = false, note = '') =>
    req<Run>(`/runs/${id}/units/${program}/decision`, json({ decision, force, note })),
  setLLM: (api_key: string | null, model?: string, persist = false, limits?: { rpm?: number; tpm?: number }) =>
    req<Health['llm'] & { persisted: boolean }>('/settings/llm', json({ api_key, model, persist, ...limits })),
  resetQuota: () => req<Health['llm']>('/settings/llm/reset-quota', { method: 'POST' }),
  testLLM: (api_key: string | null, model?: string) =>
    req<{ ok: boolean; models: string[]; model_available: boolean }>('/settings/llm/test', json({ api_key, model })),
  models: () => req<{ models: string[]; current: string }>('/llm/models'),
}
