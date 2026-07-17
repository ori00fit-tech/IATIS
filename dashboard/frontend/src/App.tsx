import { useEffect, useState, type ReactNode } from 'react'
import { AuthProvider, useAuth } from './lib/auth'
import { Login } from './pages/Login'
import { TABS, type TabId } from './lib/tabs'
import { useHashTab } from './lib/useHashTab'
import { ErrorBoundary } from './components/ErrorBoundary'
import { CommandPalette } from './components/CommandPalette'
import { MissionControl } from './modules/mission-control/MissionControl'
import { LiveSignals } from './modules/live-signals/LiveSignals'
import { DataCenter } from './modules/data-center/DataCenter'
import { EngineMonitor } from './modules/engine-monitor/EngineMonitor'
import { ResearchBacktests } from './modules/research-backtests/ResearchBacktests'
import { BacktestingCharts } from './modules/backtesting-charts/BacktestingCharts'
import { SystemAudit } from './modules/system-audit/SystemAudit'
import { LiveLogs } from './modules/live-logs/LiveLogs'
import { FileExplorer } from './modules/file-explorer/FileExplorer'
import { AlertCenter } from './modules/alert-center/AlertCenter'
import { ForwardDemo } from './modules/forward-demo/ForwardDemo'
import { RiskCenter } from './modules/risk-center/RiskCenter'
import { ExecutionQuality } from './modules/execution-quality/ExecutionQuality'
import { Reports } from './modules/reports/Reports'
import { ExperimentRunner } from './modules/experiment-runner/ExperimentRunner'
import { VpsOperations } from './modules/vps-operations/VpsOperations'
import { RoadmapGrid } from './modules/roadmap/RoadmapGrid'

const MODULES: Record<TabId, () => ReactNode> = {
  'mission-control': () => <MissionControl />,
  'live-signals': () => <LiveSignals />,
  'forward-demo': () => <ForwardDemo />,
  'risk-center': () => <RiskCenter />,
  'execution-quality': () => <ExecutionQuality />,
  'data-center': () => <DataCenter />,
  'engine-monitor': () => <EngineMonitor />,
  research: () => <ResearchBacktests />,
  'backtesting-charts': () => <BacktestingCharts />,
  'system-audit': () => <SystemAudit />,
  logs: () => <LiveLogs />,
  files: () => <FileExplorer />,
  alerts: () => <AlertCenter />,
  reports: () => <Reports />,
  experiments: () => <ExperimentRunner />,
  ops: () => <VpsOperations />,
  roadmap: () => <RoadmapGrid />,
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

  return (
    <div className="min-h-screen">
      <header className="flex items-center justify-between px-6 py-4 border-b border-border bg-gradient-to-r from-bg to-[#0d1829]">
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

      <nav className="flex gap-1 px-6 pt-3 border-b border-border overflow-x-auto" aria-label="Modules">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id as TabId)}
            aria-current={tab === t.id ? 'page' : undefined}
            className={`px-3 py-2 text-[0.78em] whitespace-nowrap border-b-2 -mb-px transition-colors ${
              tab === t.id ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-text'
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="px-6 py-5 max-w-[1400px] mx-auto">
        <ErrorBoundary key={tab} moduleName={TABS.find((t) => t.id === tab)?.label ?? tab}>
          {MODULES[tab]()}
        </ErrorBoundary>
      </main>

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
    <AuthProvider>
      <Root />
    </AuthProvider>
  )
}
