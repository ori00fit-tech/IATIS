// Small statistics helpers for the Evidence Progress panel (v0.6 spec §2).
// Pure functions — the point estimate is secondary to the interval, so these
// exist to make the uncertainty explicit rather than hide it behind a number.

export interface Interval {
  center: number // proportion in [0,1]
  low: number
  high: number
}

/**
 * Wilson score interval for a binomial proportion — the correct small-sample
 * CI for a win rate (unlike the naive normal approximation, it stays inside
 * [0,1] and behaves at the extremes). z defaults to 1.96 (95%).
 */
export function wilsonInterval(successes: number, n: number, z = 1.96): Interval | null {
  if (n <= 0) return null
  const p = successes / n
  const z2 = z * z
  const denom = 1 + z2 / n
  const center = (p + z2 / (2 * n)) / denom
  const margin = (z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / denom
  return {
    center,
    low: Math.max(0, center - margin),
    high: Math.min(1, center + margin),
  }
}

/**
 * Largest peak-to-trough decline of a per-step series, in the series' own
 * units (here: R-multiples). Expressed in R rather than as a fraction so it
 * needs no starting-balance assumption — the paper book exposes no equity
 * baseline. Returns 0 for a monotonically rising (or empty) series.
 */
export function maxDrawdownR(steps: number[]): number {
  let cum = 0
  let peak = 0
  let maxDD = 0
  for (const r of steps) {
    cum += r
    if (cum > peak) peak = cum
    const dd = peak - cum
    if (dd > maxDD) maxDD = dd
  }
  return maxDD
}
