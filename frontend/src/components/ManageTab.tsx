import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, Save } from 'lucide-react'
import { portfolioApi } from '@/lib/api'
import type { Holding, Portfolio } from '@/lib/types'
import { fmt } from '@/lib/utils'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from './ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'

// ─── Holding Form ─────────────────────────────────────────────────────────────

const EMPTY: Omit<Holding, 'trailing_stop_pct'> & { trailing_stop_pct: string } = {
  ticker: '', shares: 0 as unknown as number, avg_cost: 0 as unknown as number,
  entry_date: new Date().toISOString().slice(0, 10), thesis: '',
  stop_loss: null, take_profit: null, peak_price: null, trailing_stop_pct: '0.08',
}

function HoldingForm({
  initial, onSave, onClose,
}: {
  initial?: Holding
  onSave: (h: Holding) => void
  onClose: () => void
}) {
  const [form, setForm] = useState({
    ticker:           initial?.ticker ?? '',
    shares:           String(initial?.shares ?? ''),
    avg_cost:         String(initial?.avg_cost ?? ''),
    entry_date:       initial?.entry_date ?? new Date().toISOString().slice(0, 10),
    thesis:           initial?.thesis ?? '',
    stop_loss:        String(initial?.stop_loss ?? ''),
    take_profit:      String(initial?.take_profit ?? ''),
    trailing_stop_pct: String(initial?.trailing_stop_pct ?? '0.08'),
  })

  const field = (k: keyof typeof form) => ({
    value: form[k],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      setForm(f => ({ ...f, [k]: e.target.value })),
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const h: Holding = {
      ticker:           form.ticker.toUpperCase().trim(),
      shares:           parseFloat(form.shares),
      avg_cost:         parseFloat(form.avg_cost),
      entry_date:       form.entry_date,
      thesis:           form.thesis,
      stop_loss:        form.stop_loss ? parseFloat(form.stop_loss) : null,
      take_profit:      form.take_profit ? parseFloat(form.take_profit) : null,
      peak_price:       initial?.peak_price ?? null,
      trailing_stop_pct: parseFloat(form.trailing_stop_pct),
    }
    if (!h.ticker || isNaN(h.shares) || isNaN(h.avg_cost)) return
    onSave(h)
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label>Ticker *</Label>
          <Input {...field('ticker')} placeholder="AAPL" className="uppercase" required />
        </div>
        <div className="space-y-1.5">
          <Label>Entry Date *</Label>
          <Input type="date" {...field('entry_date')} required />
        </div>
        <div className="space-y-1.5">
          <Label>Shares *</Label>
          <Input type="number" step="any" {...field('shares')} placeholder="100" required />
        </div>
        <div className="space-y-1.5">
          <Label>Avg Cost ($/share) *</Label>
          <Input type="number" step="any" {...field('avg_cost')} placeholder="150.00" required />
        </div>
        <div className="space-y-1.5">
          <Label>Stop Loss ($)</Label>
          <Input type="number" step="any" {...field('stop_loss')} placeholder="Optional" />
        </div>
        <div className="space-y-1.5">
          <Label>Take Profit ($)</Label>
          <Input type="number" step="any" {...field('take_profit')} placeholder="Optional" />
        </div>
        <div className="space-y-1.5">
          <Label>Trailing Stop %</Label>
          <Input type="number" step="0.01" {...field('trailing_stop_pct')} placeholder="0.08" />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label>Investment Thesis</Label>
        <Input {...field('thesis')} placeholder="Why did you buy this?" />
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
        <Button type="submit">Save Holding</Button>
      </DialogFooter>
    </form>
  )
}

// ─── Manage Tab ───────────────────────────────────────────────────────────────

export function ManageTab() {
  const qc = useQueryClient()
  const [editHolding, setEditHolding] = useState<Holding | null>(null)
  const [isAdding, setIsAdding] = useState(false)
  const [accountSize, setAccountSize] = useState('')

  const { data: portfolio, isLoading } = useQuery({
    queryKey: ['portfolio'],
    queryFn:  portfolioApi.get,
    staleTime: 30_000,
  })

  // Sync account size
  useState(() => {
    if (portfolio?.account_size && !accountSize)
      setAccountSize(String(portfolio.account_size))
  })

  const save = useMutation({
    mutationFn: (p: Portfolio) => portfolioApi.save(p),
    onSuccess:  () => {
      qc.invalidateQueries({ queryKey: ['portfolio'] })
      qc.invalidateQueries({ queryKey: ['portfolio-results'] })
    },
  })

  if (isLoading || !portfolio) {
    return <div className="text-center py-12 text-muted-foreground">Loading…</div>
  }

  const holdings = portfolio.holdings

  function updateAndSave(newHoldings: Holding[]) {
    save.mutate({
      account_size: parseFloat(accountSize) || portfolio!.account_size,
      holdings: newHoldings,
    })
  }

  function addHolding(h: Holding) {
    updateAndSave([...holdings.filter(x => x.ticker !== h.ticker), h])
    setIsAdding(false)
  }

  function editDone(h: Holding) {
    updateAndSave(holdings.map(x => x.ticker === h.ticker ? h : x))
    setEditHolding(null)
  }

  function remove(ticker: string) {
    if (!confirm(`Remove ${ticker} from portfolio?`)) return
    updateAndSave(holdings.filter(h => h.ticker !== ticker))
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Manage Holdings</h2>
        <Button size="sm" onClick={() => setIsAdding(true)} className="gap-1">
          <Plus className="h-4 w-4" /> Add Holding
        </Button>
      </div>

      {/* Account size */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-4">
            <div className="space-y-1 flex-1 max-w-xs">
              <Label>Account Size ($)</Label>
              <Input
                type="number"
                value={accountSize || portfolio.account_size}
                onChange={e => setAccountSize(e.target.value)}
                placeholder="50000"
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              className="mt-5"
              onClick={() => updateAndSave(holdings)}
            >
              <Save className="h-4 w-4 mr-1" /> Save
            </Button>
          </div>
        </CardContent>
      </Card>

      {save.isError && (
        <p className="text-sm text-red-400">{(save.error as Error).message}</p>
      )}
      {save.isSuccess && (
        <p className="text-sm text-emerald-400">Portfolio saved.</p>
      )}

      {/* Holdings table */}
      {holdings.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border p-12 text-center">
          <p className="text-muted-foreground">No holdings yet.</p>
          <p className="text-xs text-muted-foreground mt-1">Click "Add Holding" to get started.</p>
        </div>
      ) : (
        <div className="rounded-md border border-border/50 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ticker</TableHead>
                <TableHead>Shares</TableHead>
                <TableHead>Avg Cost</TableHead>
                <TableHead>Cost Basis</TableHead>
                <TableHead className="hidden sm:table-cell">Entry Date</TableHead>
                <TableHead className="hidden md:table-cell">Stop Loss</TableHead>
                <TableHead className="hidden md:table-cell">Take Profit</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {holdings.map(h => (
                <TableRow key={h.ticker}>
                  <TableCell className="font-semibold">{h.ticker}</TableCell>
                  <TableCell>{h.shares}</TableCell>
                  <TableCell>${fmt(h.avg_cost)}</TableCell>
                  <TableCell>${fmt(h.shares * h.avg_cost)}</TableCell>
                  <TableCell className="hidden sm:table-cell text-muted-foreground">{h.entry_date}</TableCell>
                  <TableCell className="hidden md:table-cell text-muted-foreground">
                    {h.stop_loss ? `$${fmt(h.stop_loss)}` : '—'}
                  </TableCell>
                  <TableCell className="hidden md:table-cell text-muted-foreground">
                    {h.take_profit ? `$${fmt(h.take_profit)}` : '—'}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      <Button variant="ghost" size="icon" onClick={() => setEditHolding(h)}>
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button variant="ghost" size="icon" onClick={() => remove(h.ticker)}
                        className="text-destructive hover:text-destructive">
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Add dialog */}
      <Dialog open={isAdding} onOpenChange={setIsAdding}>
        <DialogContent>
          <DialogHeader><DialogTitle>Add Holding</DialogTitle></DialogHeader>
          <HoldingForm onSave={addHolding} onClose={() => setIsAdding(false)} />
        </DialogContent>
      </Dialog>

      {/* Edit dialog */}
      <Dialog open={!!editHolding} onOpenChange={v => !v && setEditHolding(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>Edit {editHolding?.ticker}</DialogTitle></DialogHeader>
          {editHolding && (
            <HoldingForm
              initial={editHolding}
              onSave={editDone}
              onClose={() => setEditHolding(null)}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
