import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Play, Square, ChevronDown, ChevronRight, Clock } from 'lucide-react'
import { scanApi, universeApi } from '@/lib/api'
import type { StockReport, ScanJob, ScanRec } from '@/lib/types'
import { fmt, fmtScore, fmtUsd, timeAgo } from '@/lib/utils'
import { Button } from './ui/button'
import { Badge } from './ui/badge'
import { Progress } from './ui/progress'
import { Textarea } from './ui/textarea'
import { Separator } from './ui/separator'
import { Card, CardContent } from './ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function recBadge(rec: ScanRec) {
  if (rec === 'Buy')   return <Badge variant="buy">● Buy</Badge>
  if (rec === 'Watch') return <Badge variant="watch">◎ Watch</Badge>
  return                      <Badge variant="avoid">✗ Avoid</Badge>
}

function ScoreCell({ value }: { value: number }) {
  const color = value >= 80 ? 'text-emerald-400' : value >= 60 ? 'text-yellow-400' : 'text-red-400'
  return (
    <div className="flex items-center gap-2">
      <div className="w-14 h-1.5 rounded-full bg-secondary overflow-hidden">
        <div
          className={`h-full rounded-full ${value >= 80 ? 'bg-emerald-500' : value >= 60 ? 'bg-yellow-500' : 'bg-red-500'}`}
          style={{ width: `${value}%` }}
        />
      </div>
      <span className={`text-xs font-medium ${color}`}>{fmtScore(value)}</span>
    </div>
  )
}

function FactorCell({ value }: { value: number }) {
  return <span className="text-xs text-muted-foreground">{fmtScore(value)}</span>
}

// ─── Expanded row ─────────────────────────────────────────────────────────────

function ExpandedRow({ report }: { report: StockReport }) {
  const ind = report.indicators
  const tp  = report.trade_plan
  return (
    <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-3 text-xs bg-secondary/20">
      <div>
        <div className="font-medium mb-2 text-muted-foreground uppercase tracking-wider text-[10px]">Indicators</div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1">
          <span className="text-muted-foreground">RSI</span>          <span>{fmt(ind.rsi, 1)}</span>
          <span className="text-muted-foreground">ADX</span>          <span>{fmt(ind.adx, 1)}</span>
          <span className="text-muted-foreground">ATR%</span>         <span>{ind.atr_pct != null ? `${fmt(ind.atr_pct)}%` : '—'}</span>
          <span className="text-muted-foreground">MACD hist</span>    <span>{fmt(ind.macd?.histogram, 3)}</span>
          <span className="text-muted-foreground">Stoch K</span>      <span>{fmt(ind.stoch?.k, 1)}</span>
          <span className="text-muted-foreground">Close</span>        <span>${fmt(ind.close)}</span>
          <span className="text-muted-foreground">Volume</span>       <span>{ind.volume ? (ind.volume / 1000).toFixed(0) + 'K' : '—'}</span>
        </div>
      </div>
      <div>
        <div className="font-medium mb-2 text-muted-foreground uppercase tracking-wider text-[10px]">Factor Scores</div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1">
          {Object.entries(report.factor_scores).map(([k, v]) => (
            <>
              <span key={k + '_k'} className="text-muted-foreground capitalize">{k.replace('_', ' ')}</span>
              <span key={k + '_v'} className={v < 0 ? 'text-red-400' : ''}>{fmt(v, 1)}</span>
            </>
          ))}
        </div>
      </div>
      <div>
        <div className="font-medium mb-2 text-muted-foreground uppercase tracking-wider text-[10px]">Trade Plan</div>
        {tp ? (
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            <span className="text-muted-foreground">Entry</span>      <span>${fmt(tp.entry)}</span>
            <span className="text-muted-foreground">Stop Loss</span>  <span>${fmt(tp.stop_loss)}</span>
            <span className="text-muted-foreground">Take Profit</span><span>${fmt(tp.take_profit)}</span>
            <span className="text-muted-foreground">Shares</span>     <span>{tp.position_size_shares}</span>
            <span className="text-muted-foreground">Risk</span>       <span>{fmtUsd(tp.risk_amount_usd)}</span>
            <span className="text-muted-foreground">R:R</span>        <span>{fmt(tp.reward_risk_ratio, 1)}:1</span>
          </div>
        ) : (
          <p className="text-muted-foreground">{report.risk_note || 'No trade plan.'}</p>
        )}
        {report.explanation && (
          <p className="mt-3 text-muted-foreground leading-relaxed">{report.explanation}</p>
        )}
      </div>
    </div>
  )
}

// ─── Results table ────────────────────────────────────────────────────────────

function ResultsTable({ reports }: { reports: StockReport[] }) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const [filterRec, setFilterRec] = useState<ScanRec | 'All'>('All')
  const [minScore, setMinScore] = useState(0)

  const filtered = reports.filter(r => {
    if (filterRec !== 'All' && r.recommendation !== filterRec) return false
    if (r.score < minScore) return false
    return true
  })

  const counts = { Buy: 0, Watch: 0, Avoid: 0 }
  reports.forEach(r => { counts[r.recommendation]++ })

  return (
    <div className="space-y-3">
      {/* Summary + filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-2">
          {(['All', 'Buy', 'Watch', 'Avoid'] as const).map(f => (
            <button
              key={f}
              onClick={() => setFilterRec(f as typeof filterRec)}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${filterRec === f ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground'}`}
            >
              {f}
              {f !== 'All' && <span className="ml-1 opacity-70">{counts[f]}</span>}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground ml-auto">
          <span>Min score</span>
          <input
            type="range" min={0} max={100} step={5} value={minScore}
            onChange={e => setMinScore(Number(e.target.value))}
            className="w-24 accent-primary"
          />
          <span className="w-6">{minScore}</span>
        </div>
        <span className="text-xs text-muted-foreground">{filtered.length} results</span>
      </div>

      <div className="rounded-md border border-border/50 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Ticker</TableHead>
              <TableHead>Signal</TableHead>
              <TableHead>Score</TableHead>
              <TableHead className="hidden sm:table-cell">Confidence</TableHead>
              <TableHead className="hidden md:table-cell">Trend</TableHead>
              <TableHead className="hidden md:table-cell">Momentum</TableHead>
              <TableHead className="hidden lg:table-cell">Volume</TableHead>
              <TableHead className="hidden lg:table-cell">Entry</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map(r => (
              <>
                <TableRow
                  key={r.ticker}
                  className="cursor-pointer"
                  onClick={() => setExpanded(e => e === r.ticker ? null : r.ticker)}
                >
                  <TableCell className="pr-0">
                    {expanded === r.ticker
                      ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                      : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                    }
                  </TableCell>
                  <TableCell className="font-medium">{r.ticker}</TableCell>
                  <TableCell>{recBadge(r.recommendation)}</TableCell>
                  <TableCell><ScoreCell value={r.score} /></TableCell>
                  <TableCell className="hidden sm:table-cell"><FactorCell value={r.confidence} /></TableCell>
                  <TableCell className="hidden md:table-cell"><FactorCell value={r.factor_scores.trend} /></TableCell>
                  <TableCell className="hidden md:table-cell"><FactorCell value={r.factor_scores.momentum} /></TableCell>
                  <TableCell className="hidden lg:table-cell"><FactorCell value={r.factor_scores.volume} /></TableCell>
                  <TableCell className="hidden lg:table-cell"><FactorCell value={r.factor_scores.entry_timing} /></TableCell>
                </TableRow>
                {expanded === r.ticker && (
                  <TableRow key={r.ticker + '_exp'}>
                    <TableCell colSpan={9} className="p-0">
                      <ExpandedRow report={r} />
                    </TableCell>
                  </TableRow>
                )}
              </>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

// ─── Progress view ────────────────────────────────────────────────────────────

function ScanProgress({ job, onCancel }: { job: ScanJob; onCancel: () => void }) {
  const pct = job.ticker_count > 0 ? (job.done_count / job.ticker_count) * 100 : 0
  const elapsed = Math.round((Date.now() - new Date(job.created_at).getTime()) / 1000)
  const eta = job.done_count > 0 && job.ticker_count > job.done_count
    ? Math.round((elapsed / job.done_count) * (job.ticker_count - job.done_count))
    : null

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="pt-6 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Scanning in background…</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {job.done_count} / {job.ticker_count} tickers
                {job.current_ticker && ` · ${job.current_ticker}`}
              </p>
            </div>
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" /> {elapsed}s elapsed
              </span>
              {eta != null && <span>~{eta}s left</span>}
              <Button variant="outline" size="sm" onClick={onCancel}>
                <Square className="h-3 w-3 mr-1" /> Cancel
              </Button>
            </div>
          </div>
          <Progress value={pct} />
        </CardContent>
      </Card>

      {job.partial_results.length > 0 && (
        <div>
          <p className="text-xs text-muted-foreground mb-3">Partial results — updating live</p>
          <ResultsTable reports={job.partial_results} />
        </div>
      )}
    </div>
  )
}

// ─── Scanner Tab ──────────────────────────────────────────────────────────────

type ScanMode = 'custom' | 'sector'

export function ScannerTab() {
  const qc = useQueryClient()
  const [mode, setMode] = useState<ScanMode>('custom')
  const [tickerInput, setTickerInput] = useState('')
  const [selectedSectors, setSelectedSectors] = useState<string[]>([])
  const [activeJobId, setActiveJobId] = useState<number | null>(null)
  const [results, setResults] = useState<StockReport[] | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Load default tickers once
  const { data: defaultTickers } = useQuery({
    queryKey: ['default-tickers'],
    queryFn:  scanApi.defaultTickers,
    staleTime: Infinity,
  })
  useEffect(() => {
    if (defaultTickers?.tickers && !tickerInput)
      setTickerInput(defaultTickers.tickers.join(', '))
  }, [defaultTickers])

  // Latest completed scan
  const { data: latestScan } = useQuery({
    queryKey: ['latest-scan'],
    queryFn:  scanApi.latest,
    staleTime: 60_000,
    enabled:  activeJobId === null,
  })
  useEffect(() => {
    if (latestScan?.reports && !results) setResults(latestScan.reports)
  }, [latestScan])

  // Sector list
  const { data: sectorsData } = useQuery({
    queryKey: ['sectors'],
    queryFn:  universeApi.sectors,
    staleTime: 3_600_000,
  })
  const sectors = sectorsData?.sectors ?? []

  // Active job polling
  const { data: activeJob } = useQuery({
    queryKey: ['active-job', activeJobId],
    queryFn:  () => activeJobId ? scanApi.job(activeJobId) : scanApi.active(),
    enabled:  activeJobId !== null,
    refetchInterval: 3000,
  })

  // Check for active job on mount
  useQuery({
    queryKey: ['active-job-init'],
    queryFn:  async () => {
      const job = await scanApi.active()
      if (job && (job.status === 'pending' || job.status === 'running'))
        setActiveJobId(job.id)
      return job
    },
    staleTime: 0,
  })

  // When job completes
  useEffect(() => {
    if (!activeJob) return
    if (activeJob.status === 'complete') {
      setResults(activeJob.partial_results)
      setActiveJobId(null)
      qc.invalidateQueries({ queryKey: ['latest-scan'] })
    } else if (activeJob.status === 'error' || activeJob.status === 'cancelled') {
      setActiveJobId(null)
    }
  }, [activeJob?.status])

  // Universe ticker count preview
  const { data: sectorTickers } = useQuery({
    queryKey: ['sector-tickers', selectedSectors],
    queryFn:  () => universeApi.tickers(selectedSectors),
    enabled:  mode === 'sector',
    staleTime: 60_000,
  })

  const startScan = useMutation({
    mutationFn: async () => {
      let tickers: string[]
      if (mode === 'custom') {
        tickers = tickerInput.split(/[\s,]+/).map(t => t.trim().toUpperCase()).filter(Boolean)
      } else {
        tickers = sectorTickers?.tickers ?? []
      }
      if (!tickers.length) throw new Error('No tickers to scan')
      return scanApi.start(tickers)
    },
    onSuccess: ({ job_id }) => {
      setActiveJobId(job_id)
      setResults(null)
    },
  })

  const cancel = useMutation({
    mutationFn: () => scanApi.cancel(activeJobId!),
    onSuccess:  () => setActiveJobId(null),
  })

  const isRunning = activeJobId !== null &&
    (activeJob?.status === 'pending' || activeJob?.status === 'running')

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Opportunity Scanner</h2>
        {!isRunning && (
          <p className="text-xs text-muted-foreground">
            Scans run in the background — safe to close tab
          </p>
        )}
      </div>

      {/* Mode + input */}
      {!isRunning && (
        <div className="space-y-4">
          <div className="flex gap-2">
            {(['custom', 'sector'] as ScanMode[]).map(m => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`px-4 py-1.5 rounded-full text-sm transition-colors ${mode === m ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground'}`}
              >
                {m === 'custom' ? 'Custom Tickers' : 'By Sector (S&P 500 + NDX)'}
              </button>
            ))}
          </div>

          {mode === 'custom' ? (
            <Textarea
              value={tickerInput}
              onChange={e => setTickerInput(e.target.value)}
              placeholder="AAPL, MSFT, NVDA, GOOGL …"
              className="font-mono h-24 resize-none"
            />
          ) : (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-2 rounded-md border border-input bg-transparent p-3 min-h-[60px]">
                {sectors.map(s => (
                  <button
                    key={s}
                    onClick={() => setSelectedSectors(prev =>
                      prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]
                    )}
                    className={`px-2.5 py-1 rounded text-xs transition-colors ${selectedSectors.includes(s) ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-accent'}`}
                  >
                    {s}
                  </button>
                ))}
                {sectors.length === 0 && (
                  <span className="text-xs text-muted-foreground">Loading sectors…</span>
                )}
              </div>
              {sectorTickers && (
                <p className="text-xs text-muted-foreground">
                  {sectorTickers.count} tickers from {selectedSectors.length > 0 ? selectedSectors.join(', ') : 'all sectors'}
                </p>
              )}
            </div>
          )}

          <Button
            onClick={() => startScan.mutate()}
            disabled={startScan.isPending}
            className="gap-2"
          >
            <Play className="h-4 w-4" />
            Run Scan
          </Button>

          {startScan.isError && (
            <p className="text-sm text-red-400">{(startScan.error as Error).message}</p>
          )}
        </div>
      )}

      {/* Progress */}
      {isRunning && activeJob && (
        <ScanProgress job={activeJob} onCancel={() => cancel.mutate()} />
      )}

      {/* Results */}
      {!isRunning && results && results.length > 0 && (
        <div className="space-y-3">
          <Separator />
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">Results</h3>
            {latestScan?.meta && (
              <span className="text-xs text-muted-foreground">
                {latestScan.meta.ticker_count} tickers · {timeAgo(latestScan.meta.scanned_at)}
              </span>
            )}
          </div>
          <ResultsTable reports={results} />
        </div>
      )}
    </div>
  )
}
