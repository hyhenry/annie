import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { configApi } from '@/lib/api'
import { fmtPct } from '@/lib/utils'
import { Switch } from './ui/switch'
import { Label } from './ui/label'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'
import { Separator } from './ui/separator'

export function SettingsTab() {
  const qc = useQueryClient()

  const { data: cfg, isLoading } = useQuery({
    queryKey: ['config'],
    queryFn:  configApi.get,
    staleTime: 60_000,
  })

  const toggleMock = useMutation({
    mutationFn: (enabled: boolean) => configApi.setMock(enabled),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['config'] }),
  })

  if (isLoading || !cfg) {
    return <div className="text-center py-12 text-muted-foreground">Loading…</div>
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <h2 className="text-xl font-semibold">Settings</h2>

      {/* Mock mode */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Data Source</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <Label htmlFor="mock-toggle">Mock Mode</Label>
              <p className="text-xs text-muted-foreground mt-0.5">
                Use built-in sample data instead of live Yahoo Finance data.
                No internet required.
              </p>
            </div>
            <Switch
              id="mock-toggle"
              checked={cfg.mock_mode}
              onCheckedChange={(v) => toggleMock.mutate(v)}
            />
          </div>
          <Separator />
          <div className="text-xs text-muted-foreground space-y-1">
            <p><span className="font-medium text-foreground">Active source:</span> yfinance — Yahoo Finance + local indicator computation</p>
            <p><span className="font-medium text-foreground">Primary timeframe:</span> {cfg.intervals.primary}</p>
            <p><span className="font-medium text-foreground">Secondary timeframe:</span> {cfg.intervals.secondary}</p>
          </div>
        </CardContent>
      </Card>

      {/* Risk */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Risk Management</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-3 text-sm">
            {[
              ['Risk per trade',        `$${cfg.risk_per_trade_usd.toLocaleString()}`],
              ['Account size',          `$${cfg.account_size_usd.toLocaleString()}`],
              ['Stop-loss multiplier',  `${cfg.atr_stop_loss_multiplier}× ATR`],
              ['Take-profit multiplier',`${cfg.atr_take_profit_multiplier}× ATR`],
              ['Default trailing stop', fmtPct(cfg.trailing_stop_default_pct)],
              ['Max position size',     fmtPct(cfg.max_position_pct)],
            ].map(([k, v]) => (
              <div key={k} className="rounded-md bg-secondary/50 p-3">
                <div className="text-xs text-muted-foreground">{k}</div>
                <div className="font-medium mt-0.5">{v}</div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Scoring */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Scoring Model</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <p className="text-xs text-muted-foreground mb-2">Factor weights</p>
            <div className="space-y-2">
              {Object.entries(cfg.factor_weights).map(([k, v]) => (
                <div key={k} className="flex items-center gap-3">
                  <div className="w-32 text-xs capitalize text-muted-foreground">
                    {k.replace('_', ' ')}
                  </div>
                  <div className="flex-1 h-1.5 rounded-full bg-secondary overflow-hidden">
                    <div
                      className="h-full bg-primary rounded-full"
                      style={{ width: `${v * 100}%` }}
                    />
                  </div>
                  <div className="text-xs text-muted-foreground w-8 text-right">
                    {(v * 100).toFixed(0)}%
                  </div>
                </div>
              ))}
            </div>
          </div>
          <Separator />
          <div className="grid grid-cols-3 gap-3 text-xs">
            <div className="rounded-md bg-emerald-500/10 border border-emerald-500/20 p-3">
              <div className="text-emerald-400 font-medium">Buy</div>
              <div className="text-muted-foreground mt-0.5">Score ≥ {cfg.recommendation_bands.Buy}</div>
            </div>
            <div className="rounded-md bg-yellow-500/10 border border-yellow-500/20 p-3">
              <div className="text-yellow-400 font-medium">Watch</div>
              <div className="text-muted-foreground mt-0.5">Score ≥ {cfg.recommendation_bands.Watch}</div>
            </div>
            <div className="rounded-md bg-red-500/10 border border-red-500/20 p-3">
              <div className="text-red-400 font-medium">Avoid</div>
              <div className="text-muted-foreground mt-0.5">Score &lt; {cfg.recommendation_bands.Watch}</div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Hard filters */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Hard Filters (auto-Avoid)</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-3 text-sm">
            {[
              ['Min price',      `$${cfg.hard_filters.min_price}`],
              ['Min avg volume', `${(cfg.hard_filters.min_avg_volume / 1000).toFixed(0)}K/day`],
              ['Max ATR%',       fmtPct(cfg.hard_filters.max_atr_pct)],
            ].map(([k, v]) => (
              <div key={k} className="rounded-md bg-secondary/50 p-3">
                <div className="text-xs text-muted-foreground">{k}</div>
                <div className="font-medium mt-0.5">{v}</div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <p className="text-xs text-muted-foreground">
        To change these values, edit <code className="bg-secondary px-1 rounded">config.py</code> and restart the server.
      </p>
    </div>
  )
}
