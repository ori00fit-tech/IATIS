import { useEffect, useState } from 'react'
import { AuthProvider, useAuth } from './lib/auth'
import { Login } from './pages/Login'
import { MissionControl } from './modules/mission-control/MissionControl'
import { LiveSignals } from './modules/live-signals/LiveSignals'
import { DataCenter } from './modules/data-center/DataCenter'
import { EngineMonitor } from './modules/engine-monitor/EngineMonitor'
import { ResearchBacktests } from './modules/research-backtests/ResearchBacktests'
import { SystemAudit } from './modules/system-audit/SystemAudit'
import { LiveLogs } from './modules/live-logs/LiveLogs'
import { FileExplorer } from './modules/file-explorer/FileExplorer'
import { AlertCenter } from './modules/alert-center/AlertCenter'
import { ForwardDemo } from './modules/forward-demo/ForwardDemo'
import { RoadmapGrid } from './modules/roadmap/RoadmapGrid'

const TABS = [
  { id: 'mission-control', label: 'Mission Control' },
  { id: 'live-signals', label: 'Live Signals' },
  { id: 'forward-demo', label: 'Forward Demo' },
  { id: 'data-center', label: 'Data Center' },
  { id: 'engine-monitor', label: 'Engine Monitor' },
  { id: 'research', label: 'Research & Backtests' },
  { id: 'system-audit', label: 'System Audit' },
  { id: 'logs', label: 'Live Logs' },
  { id: 'files', label: 'File Explorer' },
  { id: 'alerts', label: 'Alert Center' },
  { id: 'roadmap', label: 'Roadmap' },
] as const

type TabId = (typeof TABS)[number]['id']

function Clock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return <span className="text-muted text-[0.78em]">{now.toUTCString().slice(0, 25)} UTC</span>
}

function Shell() {
  const { logout } = useAuth()
  const [tab, setTab] = useState<TabId>('mission-control')

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
          <Clock />
          <button onClick={() => logout()} className="text-muted text-[0.75em] bg-transparent border-none cursor-pointer hover:text-accent">
            Logout
          </button>
        </div>
      </header>

      <nav className="flex gap-1 px-6 pt-3 border-b border-border overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-[0.78em] whitespace-nowrap border-b-2 -mb-px transition-colors ${
              tab === t.id ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-text'
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="px-6 py-5 max-w-[1400px] mx-auto">
        {tab === 'mission-control' && <MissionControl />}
        {tab === 'live-signals' && <LiveSignals />}
        {tab === 'forward-demo' && <ForwardDemo />}
        {tab === 'data-center' && <DataCenter />}
        {tab === 'engine-monitor' && <EngineMonitor />}
        {tab === 'research' && <ResearchBacktests />}
        {tab === 'system-audit' && <SystemAudit />}
        {tab === 'logs' && <LiveLogs />}
        {tab === 'files' && <FileExplorer />}
        {tab === 'alerts' && <AlertCenter />}
        {tab === 'roadmap' && <RoadmapGrid />}
      </main>
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
