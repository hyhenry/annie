// ─── Scanner Types ────────────────────────────────────────────────────────────

export interface FactorScores {
  trend:           number
  momentum:        number
  volume:          number
  entry_timing:    number
  volatility:      number
  multi_timeframe: number
  risk_penalty:    number
}

export interface TradePlan {
  entry:                 number
  stop_loss:             number
  take_profit:           number
  position_size_shares:  number
  risk_amount_usd:       number
  reward_amount_usd:     number
  reward_risk_ratio:     number
}

export interface StockIndicators {
  rsi:    number | null
  macd:   { value: number; signal: number; histogram: number } | null
  adx:    number | null
  atr:    number | null
  atr_pct: number | null
  sma_20: number | null
  ema_20: number | null
  bbands: { upper: number; middle: number; lower: number } | null
  stoch:  { k: number; d: number } | null
  obv:    number | null
  close:  number | null
  volume: number | null
}

export type ScanRec = 'Buy' | 'Watch' | 'Avoid'

export interface StockReport {
  ticker:          string
  score:           number
  recommendation:  ScanRec
  confidence:      number
  factor_scores:   FactorScores
  indicators:      StockIndicators
  trade_plan:      TradePlan | null
  risk_note:       string
  explanation:     string
  filters_passed:  boolean
  filter_failures: string[]
  error:           string | null
}

export type JobStatus = 'pending' | 'running' | 'complete' | 'error' | 'cancelled'

export interface ScanJob {
  id:              number
  status:          JobStatus
  ticker_count:    number
  done_count:      number
  current_ticker:  string | null
  mock_mode:       boolean
  error:           string | null
  created_at:      string
  updated_at:      string
  partial_results: StockReport[]
}

export interface ScanMeta {
  id:           number
  scanned_at:   string
  ticker_count: number
  tickers:      string[]
  mock_mode:    boolean
  data_source:  string
}

export interface ScanResult {
  meta:    ScanMeta
  reports: StockReport[]
}

// ─── Portfolio Types ──────────────────────────────────────────────────────────

export type HoldRec = 'Hold' | 'Watch Closely' | 'Trim' | 'Sell' | 'Take Profit'

export interface Holding {
  ticker:            string
  shares:            number
  avg_cost:          number
  entry_date:        string
  thesis:            string
  stop_loss:         number | null
  take_profit:       number | null
  peak_price:        number | null
  trailing_stop_pct: number
}

export interface HoldingReport {
  holding:                Holding
  current_price:          number
  position_value_usd:     number
  unrealized_pnl_usd:     number
  unrealized_pnl_pct:     number
  hard_stop_price:        number | null
  hard_stop_breach:       boolean
  trailing_stop_price:    number
  trailing_stop_breach:   boolean
  at_profit_target:       boolean
  drawdown_from_peak_pct: number | null
  hold_quality_score:     number
  sell_risk_score:        number
  recommendation:         HoldRec
  hold_factor_scores:     Record<string, number>
  sell_risk_factors:      Record<string, number>
  indicators:             StockIndicators
  explanation:            string
  action_items:           string[]
  analysed_at:            string
  error:                  string | null
}

export interface Portfolio {
  account_size: number
  holdings:     Holding[]
}

export interface PortfolioResults {
  reports:     HoldingReport[]
  analyzed_at: string | null
  mock_mode?:  boolean
}

// ─── Config Types ─────────────────────────────────────────────────────────────

export interface AppConfig {
  mock_mode:                  boolean
  risk_per_trade_usd:         number
  account_size_usd:           number
  atr_stop_loss_multiplier:   number
  atr_take_profit_multiplier: number
  trailing_stop_default_pct:  number
  max_position_pct:           number
  intervals:                  { primary: string; secondary: string }
  recommendation_bands:       { Buy: number; Watch: number }
  factor_weights:             Record<string, number>
  hard_filters:               Record<string, number>
}
