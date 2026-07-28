import { lazy, Suspense, useEffect, useState, type LazyExoticComponent, type ReactNode } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './lib/queryClient'
import { AuthProvider, useAuth } from './lib/auth'
import { Login } from './pages/Login'
import { TABS, type TabId } from './lib/tabs'
import { useHashTab } from './lib/useHashTab'
import { ErrorBoundary } from './components/ErrorBoundary'
import { CommandPalette } from './components/CommandPalette'
import { Sidebar } from './components/Sidebar'
import { BottomPanel } from './components/BottomPanel'

// Route-level code splitting (Phase 5 institutional redesign, 2026-07-26) —
// each tab is its own dynamic import instead of one ~957KB shared bundle.
// Named-export lazy idiom matches Phase 3's existing precedent
// (RiskCenter.tsx/BacktestingCharts.tsx's MonthlyReturnsHeatmap/
// RMultipleHistogram). Specifiers MUST be string literals, not a
// templated/looped import() — only literals are statically chunk-splittable.
const MODULES: Record<TabId, LazyExoticComponent<() => ReactNode>> = {
  'mission-control': lazy(() =>
    import('./modules/mission-control/MissionControl').then((m) => ({ default: m.MissionControl }))),
  'live-signals': lazy(() =>
    import('./modules/live-signals/LiveSignals').then((m) => ({ default: m.LiveSignals }))),
  'forward-demo': lazy(() =>
    import('./modules/forward-demo/ForwardDemo').then((m) => ({ default: m.ForwardDemo }))),
  journal: lazy(() =>
    import('./modules/journal/Journal').then((m) => ({ default: m.Journal }))),
  'risk-center': lazy(() =>
    import('./modules/risk-center/RiskCenter').then((m) => ({ default: m.RiskCenter }))),
  'execution-quality': lazy(() =>
    import('./modules/execution-quality/ExecutionQuality').then((m) => ({ default: m.ExecutionQuality }))),
  'data-center': lazy(() =>
    import('./modules/data-center/DataCenter').then((m) => ({ default: m.DataCenter }))),
  'provider-eval': lazy(() =>
    import('./modules/provider-eval/ProviderEval').then((m) => ({ default: m.ProviderEval }))),
  'engine-monitor': lazy(() =>
    import('./modules/engine-monitor/EngineMonitor').then((m) => ({ default: m.EngineMonitor }))),
  'ai-decision-center': lazy(() =>
    import('./modules/ai-decision-center/AiDecisionCenter').then((m) => ({ default: m.AiDecisionCenter }))),
  'ai-settings': lazy(() =>
    import('./modules/ai-settings/AiSettings').then((m) => ({ default: m.AiSettings }))),
  research: lazy(() =>
    import('./modules/research-backtests/ResearchBacktests').then((m) => ({ default: m.ResearchBacktests }))),
  'backtesting-lab': lazy(() =>
    import('./modules/backtesting-lab/BacktestingLab').then((m) => ({ default: m.BacktestingLab }))),
  'backtesting-charts': lazy(() =>
    import('./modules/backtesting-charts/BacktestingCharts').then((m) => ({ default: m.BacktestingCharts }))),
  'system-audit': lazy(() =>
    import('./modules/system-audit/SystemAudit').then((m) => ({ default: m.SystemAudit }))),
  logs: lazy(() =>
    import('./modules/live-logs/LiveLogs').then((m) => ({ default: m.LiveLogs }))),
  files: lazy(() =>
    import('./modules/file-explorer/FileExplorer').then((m) => ({ default: m.FileExplorer }))),
  alerts: lazy(() =>
    import('./modules/alert-center/AlertCenter').then((m) => ({ default: m.AlertCenter }))),
  reports: lazy(() =>
    import('./modules/reports/Reports').then((m) => ({ default: m.Reports }))),
  experiments: lazy(() =>
    import('./modules/experiment-runner/ExperimentRunner').then((m) => ({ default: m.ExperimentRunner }))),
  'mission-center': lazy(() =>
    import('./modules/mission-center/MissionCenter').then((m) => ({ default: m.MissionCenter }))),
  ops: lazy(() =>
    import('./modules/vps-operations/VpsOperations').then((m) => ({ default: m.VpsOperations }))),
  roadmap: lazy(() =>
    import('./modules/roadmap/RoadmapGrid').then((m) => ({ default: m.RoadmapGrid }))),
}

function TabLoading({ label }: { label: string }) {
  return (
    <div className="min-h-[240px] flex items-center justify-center text-muted text-[0.85em]">
      Loading {label}…
    </div>
  )
}

function Clock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return <span className="text-muted text-[0.78em]">{now.toUTCString().slice(0, 25)} UTC</span>
}

/** Tracks tab visibility so the header can honestly show whether pollers are live. */
function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(!document.hidden)
  useEffect(() => {
    const onChange = () => setVisible(!document.hidden)
    document.addEventListener('visibilitychange', onChange)
    return () => document.removeEventListener('visibilitychange', onChange)
  }, [])
  return visible
}

function RefreshPill() {
  const visible = useDocumentVisible()
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[0.7em] px-2 py-0.5 rounded-full border ${
        visible ? 'border-green/40 text-green' : 'border-amber/40 text-amber'
      }`}
      title={visible ? 'Modules auto-refresh on their own cadence' : 'Auto-refresh paused while this tab is in the background — resumes on focus'}
    >
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${visible ? 'bg-green' : 'bg-amber'}`} />
      {visible ? 'Live' : 'Paused'}
    </span>
  )
}

function Shell() {
  const { logout } = useAuth()
  const [tab, setTab] = useHashTab()
  const [paletteOpen, setPaletteOpen] = useState(false)

  // Global ⌘K / Ctrl-K to open the jump-to-module palette.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const ActiveModule = MODULES[tab]
  const activeLabel = TABS.find((t) => t.id === tab)?.label ?? tab

  return (
    <div className="h-screen flex flex-col">
      <header className="flex items-center justify-between px-6 py-4 border-b border-border bg-gradient-to-r from-bg to-[#0d1829] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 bg-gradient-to-br from-accent to-accent2 rounded-lg flex items-center justify-center text-[18px]">
            ⚡
          </div>
          <div>
            <div className="text-[1.1em] font-bold text-accent tracking-[2px]">IATIS</div>
            <div className="text-[0.65em] text-muted tracking-[1px]">COMMAND CENTER</div>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <RefreshPill />
          <button
            onClick={() => setPaletteOpen(true)}
            className="hidden sm:inline-flex items-center gap-1.5 text-muted text-[0.72em] border border-border rounded px-2 py-1 hover:text-accent hover:border-accent/50"
            title="Jump to module"
          >
            <span>⌕ Jump</span>
            <kbd className="text-[0.9em] opacity-70">⌘K</kbd>
          </button>
          <Clock />
          <button onClick={() => logout()} className="text-muted text-[0.75em] bg-transparent border-none cursor-pointer hover:text-accent">
            Logout
          </button>
        </div>
      </header>

      <div className="flex flex-1 min-h-0">
        <Sidebar tab={tab} setTab={setTab} />
        <BottomPanel>
          <main className="px-6 py-5 max-w-[1400px] mx-auto">
            <ErrorBoundary key={tab} moduleName={activeLabel}>
              <Suspense fallback={<TabLoading label={activeLabel} />}>
                <ActiveModule />
              </Suspense>
            </ErrorBoundary>
          </main>
        </BottomPanel>
      </div>

      <CommandPalette open={paletteOpen} activeTab={tab} onSelect={setTab} onClose={() => setPaletteOpen(false)} />
    </div>
  )
}

function Root() {
  const { status } = useAuth()
  if (status === 'checking') return <div className="min-h-screen flex items-center justify-center text-muted">Connecting to IATIS...</div>
  if (status === 'unauthenticated') return <Login />
  return <Shell />
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Root />
      </AuthProvider>
    </QueryClientProvider>
  )
}
