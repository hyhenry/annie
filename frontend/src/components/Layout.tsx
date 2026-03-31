import { TrendingUp } from 'lucide-react'
import { Badge } from './ui/badge'

interface LayoutProps {
  children: React.ReactNode
  mockMode: boolean
}

export function Layout({ children, mockMode }: LayoutProps) {
  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border/50 bg-card/50 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4">
          <TrendingUp className="h-5 w-5 text-primary" />
          <span className="font-semibold tracking-tight">Annie</span>
          <span className="text-xs text-muted-foreground">Stock Intelligence</span>
          {mockMode && (
            <Badge variant="secondary" className="ml-2 text-xs">
              Mock Mode
            </Badge>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        {children}
      </main>
    </div>
  )
}
