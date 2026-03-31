import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, AlertTriangle, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { portfolioApi } from '@/lib/api'
import type { HoldingReport, HoldRec } from '@/lib/types'
import { fmt, fmtPct, fmtUsd, fmtScore, timeAgo } from '@/lib/utils'
import { Button } from './ui/button'
import { Badge } from './ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'
import { Progress } from './ui/progress'
import { Separator } from './ui/separator'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function recVariant(rec: HoldRec) {
  if (rec === 'Hold')           return 'hold' as const
  if (rec === 'Trim')           return 'trim' as const
  if (rec === 'Sell')           return 'sell' as const
  if (rec === 'Watch Closely')  return 'watch-closely' as const
  if (rec === 'Take Profit')    return 'take-profit' as const
  return 'secondary' as const
}

function recIcon(rec: HoldRec) {
  if (rec === 'Sell')    return '⚡'
  if (rec === 'Trim')    return '✂️'
  if (rec === 'Hold')    return '✓'
  if (rec === 'Take Profit') return '💰'
  return '👁'
}

function ScoreBar({ label, value, invert = false }: { label: string; value: number; invert?: boolean }) {
  const color = invert
    ? value > 60 ? 'bg-red-500' : value > 35 ? 'bg-yellow-500' : 'bg-emerald-500'
    : value > 60 ? 'bg-emerald-500' : value > 35 ? 'bg-yellow-500' : 'bg-red-500'
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span className="font-medium text-foreground">{fmtScore(value)}</span>
      </div>
      <Progress value={value} indicatorClass={color} />
    </div>
  )
}

// ─── Holding Card ─────────────────────────────────────────────────────────────

function HoldingCard({ report }: { report: HoldingReport }) {
  const [expanded, setExpanded] = useState(false)
  const { holding, current_price, unrealized_pnl_usd, unrealized_pnl_pct,
          hold_quality_score, sell_risk_score, recommendation,
          trailing_stop_price, hard_stop_price, hard_stop_breach,
          trailing_stop_breach, action_items, explanation, error } = report

  const pnlPositive = unrealized_pnl_usd >= 0

  return (
    <Card className={hard_stop_breach || trailing_stop_breach ? 'border-red-500/50' : ''}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-lg font-bold">{holding.ticker}</span>
                <Badge variant={recVariant(recommendation)}>
                  {recIcon(recommendation)} {recommendation}
                </Badge>
                {(hard_stop_breach || trailing_stop_breach) && (
                  <Badge variant="destructive" className="gap-1">
                    <AlertTriangle className="h-3 w-3" /> Stop Breach
                  </Badge>
                )}
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">
                {holding.shares} shares · avg ${fmt(holding.avg_cost)}
              </p>
            </div>
          </div>
          <div className="text-right">
            <div className="text-xl font-semibold">${fmt(current_price)}</div>
            <div className={`flex items-center justify-end gap-1 text-sm ${pnlPositive ? 'text-emerald-400' : 'text-red-400'}`}>
              {pnlPositive ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
              {fmtUsd(unrealized_pnl_usd)} ({fmtPct(unrealized_pnl_pct)})
            </div>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {error && (
          <p className="text-xs text-red-400 bg-red-500/10 rounded p-2">{error}</p>
        )}

        {/* Scores */}
        <div className="grid grid-cols-2 gap-4">
          <ScoreBar label="Hold Quality" value={hold_quality_score} />
          <ScoreBar label="Sell Risk"    value={sell_risk_score}    invert />
        </div>

        {/* Stop levels */}
        <div className="grid grid-cols-3 gap-3 text-xs">
          <div className="rounded-md bg-secondary/50 p-2">
            <div className="text-muted-foreground mb-1">Trailing Stop</div>
            <div className={`font-semibold ${trailing_stop_breach ? 'text-red-400' : ''}`}>
              ${fmt(trailing_stop_price)}
            </div>
          </div>
          <div className="rounded-md bg-secondary/50 p-2">
            <div className="text-muted-foreground mb-1">Hard Stop</div>
            <div className={`font-semibold ${hard_stop_breach ? 'text-red-400' : ''}`}>
              {hard_stop_price ? `$${fmt(hard_stop_price)}` : '—'}
            </div>
          </div>
          <div className="rounded-md bg-secondary/50 p-2">
            <div className="text-muted-foreground mb-1">Take Profit</div>
            <div className="font-semibold">
              {holding.take_profit ? `$${fmt(holding.take_profit)}` : '—'}
            </div>
          </div>
        </div>

        {/* Action items */}
        {action_items.length > 0 && (
          <ul className="space-y-1">
            {action_items.map((item, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
                <span className="mt-0.5 text-primary">•</span>
                {item}
              </li>
            ))}
          </ul>
        )}

        {/* Expand / collapse */}
        <button
          onClick={() => setExpanded(v => !v)}
          className="w-full text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          {expanded ? '▲ Less detail' : '▼ Full analysis'}
        </button>

        {expanded && (
          <>
            <Separator />
            <p className="text-xs text-muted-foreground leading-relaxed">{explanation}</p>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
              <span className="text-muted-foreground">RSI</span>
              <span>{fmt(report.indicators.rsi, 1)}</span>
              <span className="text-muted-foreground">ADX</span>
              <span>{fmt(report.indicators.adx, 1)}</span>
              <span className="text-muted-foreground">ATR%</span>
              <span>{report.indicators.atr_pct != null ? `${fmt(report.indicators.atr_pct)}%` : '—'}</span>
              <span className="text-muted-foreground">Close</span>
              <span>${fmt(report.indicators.close)}</span>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

// ─── Portfolio Tab ────────────────────────────────────────────────────────────

export function PortfolioTab() {
  const qc = useQueryClient()

  const { data: results, isLoading } = useQuery({
    queryKey:  ['portfolio-results'],
    queryFn:   portfolioApi.results,
    staleTime: 30_000,
  })

  const analyze = useMutation({
    mutationFn: () => portfolioApi.analyze(),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['portfolio-results'] }),
  })

  const reports = results?.reports ?? []
  const analyzedAt = results?.analyzed_at

  const totalValue = reports.reduce((s, r) => s + r.position_value_usd, 0)
  const totalPnl   = reports.reduce((s, r) => s + r.unrealized_pnl_usd, 0)

  const counts = reports.reduce((acc, r) => {
    acc[r.recommendation] = (acc[r.recommendation] ?? 0) + 1
    return acc
  }, {} as Record<string, number>)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">Portfolio Monitor</h2>
          {analyzedAt && (
            <p className="text-xs text-muted-foreground mt-0.5">
              Last analysed {timeAgo(analyzedAt)}
            </p>
          )}
        </div>
        <Button onClick={() => analyze.mutate()} disabled={analyze.isPending} size="sm">
          <RefreshCw className={`h-4 w-4 mr-2 ${analyze.isPending ? 'animate-spin' : ''}`} />
          {analyze.isPending ? 'Analysing…' : 'Run Analysis'}
        </Button>
      </div>

      {analyze.isError && (
        <div className="rounded-md bg-destructive/10 border border-destructive/30 p-3 text-sm text-red-400">
          {(analyze.error as Error).message}
        </div>
      )}

      {/* Summary metrics */}
      {reports.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: 'Portfolio Value', value: fmtUsd(totalValue) },
            { label: 'Unrealised P&L',  value: fmtUsd(totalPnl), positive: totalPnl >= 0 },
            { label: 'Holdings',        value: String(reports.length) },
            { label: 'Action Needed',   value: String((counts['Sell'] ?? 0) + (counts['Trim'] ?? 0)) },
          ].map(m => (
            <Card key={m.label}>
              <CardContent className="p-4">
                <div className="text-xs text-muted-foreground">{m.label}</div>
                <div className={`text-xl font-semibold mt-1 ${m.positive === false ? 'text-red-400' : m.positive ? 'text-emerald-400' : ''}`}>
                  {m.value}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Holdings */}
      {isLoading ? (
        <div className="text-center py-12 text-muted-foreground">Loading…</div>
      ) : reports.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border p-12 text-center">
          <p className="text-muted-foreground">No portfolio results yet.</p>
          <p className="text-xs text-muted-foreground mt-1">Add holdings in the Manage tab, then click Run Analysis.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {reports.map(r => <HoldingCard key={r.holding.ticker} report={r} />)}
        </div>
      )}
    </div>
  )
}
