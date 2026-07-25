import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** shadcn/ui's standard className-merge helper — lets variant classes and
 * caller-supplied overrides compose without Tailwind specificity clashes. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
