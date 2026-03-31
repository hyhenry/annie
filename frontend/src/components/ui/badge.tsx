import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default:     'border-transparent bg-primary text-primary-foreground shadow hover:bg-primary/80',
        secondary:   'border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80',
        destructive: 'border-transparent bg-destructive text-destructive-foreground shadow hover:bg-destructive/80',
        outline:     'text-foreground',
        buy:         'border-transparent bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
        watch:       'border-transparent bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
        avoid:       'border-transparent bg-red-500/20 text-red-400 border-red-500/30',
        hold:        'border-transparent bg-blue-500/20 text-blue-400 border-blue-500/30',
        trim:        'border-transparent bg-orange-500/20 text-orange-400 border-orange-500/30',
        sell:        'border-transparent bg-red-500/20 text-red-400 border-red-500/30',
        'watch-closely': 'border-transparent bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
        'take-profit':   'border-transparent bg-purple-500/20 text-purple-400 border-purple-500/30',
      },
    },
    defaultVariants: { variant: 'default' },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
