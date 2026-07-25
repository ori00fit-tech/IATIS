import * as ScrollAreaPrimitive from '@radix-ui/react-scroll-area'
import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

export function ScrollArea({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <ScrollAreaPrimitive.Root className={cn('overflow-hidden', className)}>
      <ScrollAreaPrimitive.Viewport className="h-full w-full">{children}</ScrollAreaPrimitive.Viewport>
      <ScrollAreaPrimitive.Scrollbar orientation="vertical" className="flex w-2 touch-none select-none p-0.5">
        <ScrollAreaPrimitive.Thumb className="flex-1 rounded-full bg-border" />
      </ScrollAreaPrimitive.Scrollbar>
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  )
}
