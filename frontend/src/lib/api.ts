import type {
  AppConfig, Portfolio, PortfolioResults,
  ScanJob, ScanMeta, ScanResult, StockReport,
} from './types'

// ─── Core fetch helpers ───────────────────────────────────────────────────────

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

const get  = <T>(path: string) => apiFetch<T>(path)
const post = <T>(path: string, body?: unknown) =>
  apiFetch<T>(path, { method: 'POST', body: body != null ? JSON.stringify(body) : undefined })
const put  = <T>(path: string, body: unknown) =>
  apiFetch<T>(path, { method: 'PUT', body: JSON.stringify(body) })
const del  = <T>(path: string) => apiFetch<T>(path, { method: 'DELETE' })

// ─── Config ───────────────────────────────────────────────────────────────────

export const configApi = {
  get:        ()              => get<AppConfig>('/config'),
  setMock:    (enabled: boolean) => post<{ mock_mode: boolean }>('/config/mock', { enabled }),
}

// ─── Portfolio ────────────────────────────────────────────────────────────────

export const portfolioApi = {
  get:     ()               => get<Portfolio>('/portfolio'),
  save:    (p: Portfolio)   => put<{ saved: boolean }>('/portfolio', p),
  analyze: (mockMode?: boolean) =>
    post<PortfolioResults>('/portfolio/analyze', mockMode != null ? { mock_mode: mockMode } : undefined),
  results: ()               => get<PortfolioResults>('/portfolio/results'),
}

// ─── Scanner ──────────────────────────────────────────────────────────────────

export const scanApi = {
  start:   (tickers: string[], mock_mode?: boolean) =>
    post<{ job_id: number }>('/scan', { tickers, mock_mode }),
  active:  ()            => get<ScanJob | null>('/scan/active'),
  job:     (id: number)  => get<ScanJob>(`/scan/job/${id}`),
  cancel:  (id: number)  => del<{ cancelled: boolean }>(`/scan/job/${id}`),
  history: ()            => get<ScanMeta[]>('/scan/history'),
  latest:  ()            => get<ScanResult | null>('/scan/latest'),
  byId:    (id: number)  => get<ScanResult>(`/scan/${id}`),
  defaultTickers: ()     => get<{ tickers: string[] }>('/tickers'),
}

// ─── Universe ─────────────────────────────────────────────────────────────────

export const universeApi = {
  sectors: ()                   => get<{ sectors: string[] }>('/universe/sectors'),
  tickers: (sectors: string[])  =>
    post<{ tickers: string[]; count: number }>('/universe/tickers', { sectors }),
}
