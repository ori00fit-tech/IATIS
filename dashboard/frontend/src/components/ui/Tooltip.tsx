import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

export const TooltipProvider = TooltipPrimitive.Provider

/** Thin wrapper over Radix Tooltip — used by the collapsed sidebar rail to
 * show a nav item's label/hint on hover without sacrificing keyboard/AT
 * accessibility (Radix owns focus/ARIA wiring). */
export function Tooltip({ content, children, side = 'right' }: { content: ReactNode; children: ReactNode; side?: 'top' | 'right' | 'bottom' | 'left' }) {
  return (
    <TooltipPrimitive.Root delayDuration={200}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={8}
          className={cn('z-50 rounded border border-border bg-panel px-2.5 py-1.5 text-[0.72em] text-text shadow-md')}
        >
          {content}
          <TooltipPrimitive.Arrow className="fill-panel" />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  )
}
