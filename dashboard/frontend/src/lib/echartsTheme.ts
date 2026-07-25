import * as echarts from 'echarts/core'
import { HeatmapChart, BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

// One-time modular registration (Phase 3 institutional redesign) — only the
// series/components the two charts that exist today actually use, so the
// bundle carries a fraction of the full echarts library. Re-exported so
// MonthlyReturnsHeatmap/RMultipleHistogram pass this exact configured
// instance to echarts-for-react's tree-shakeable core component instead of
// its default auto-registering one.
echarts.use([HeatmapChart, BarChart, GridComponent, TooltipComponent, VisualMapComponent, CanvasRenderer])

export { echarts }

// Hex values mirrored from theme/tokens.css — ECharts renders to canvas, not
// the DOM, so it cannot resolve CSS custom properties (`var(--x)`) at
// option-build time. Keep these in sync with tokens.css by hand; tokens.css
// remains the single source of truth for the palette itself.
export const CHART_COLORS = {
  text: '#e2e8f0', // --text
  muted: '#64748b', // --muted
  border: '#1a2236', // --border
  cardBg: '#111827', // --card-bg
  accent: '#00d4ff', // --accent
  green: '#00e676', // --green
  red: '#ff5252', // --red
} as const

/** Shared dark-theme option fragment — merge into each chart's own option. */
export function baseOption() {
  return {
    backgroundColor: 'transparent',
    textStyle: { color: CHART_COLORS.text, fontFamily: 'inherit', fontSize: 11 },
    tooltip: {
      backgroundColor: CHART_COLORS.cardBg,
      borderColor: CHART_COLORS.border,
      textStyle: { color: CHART_COLORS.text, fontSize: 11 },
    },
  }
}
