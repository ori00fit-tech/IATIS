import * as SeparatorPrimitive from '@radix-ui/react-separator'
import { cn } from '../../lib/cn'

export function Separator({ className, orientation = 'horizontal' }: { className?: string; orientation?: 'horizontal' | 'vertical' }) {
  return (
    <SeparatorPrimitive.Root
      orientation={orientation}
      decorative
      className={cn(orientation === 'horizontal' ? 'h-px w-full' : 'w-px h-full', 'bg-border', className)}
    />
  )
}
