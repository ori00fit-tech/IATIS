import { useRef, type ReactNode } from 'react'
import { Group, Panel, Separator, type PanelImperativeHandle, type PanelSize } from 'react-resizable-panels'
import { Empty } from './Panel'
import { useUiStore, BOTTOM_PANEL_COLLAPSED_HEIGHT, BOTTOM_PANEL_MIN_HEIGHT, type BottomPanelTab } from '../lib/uiStore'
import { useLiveLogs, levelClass } from '../lib/useLiveLogs'

const TABS: { id: BottomPanelTab; label: string }[] = [
  { id: 'logs', label: 'Logs' },
  { id: 'console', label: 'Console' },
  { id: 'raw-json', label: 'Raw JSON' },
  { id: 'notes', label: 'Research Notes' },
]

/** Compact logs view — reuses the exact same fetch/poll logic as the full
 * Live Logs module tab (src/lib/useLiveLogs.ts), just a denser rendering
 * with no filter toolbar, so this is never a second, drifting log source. */
function LogsBody() {
  const { logs, sources, source, setSource } = useLiveLogs(100)
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border shrink-0">
        <select
          className="bg-surface border border-border rounded px-1.5 py-0.5 text-[0.72em] text-text"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        >
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
        {logs.data && <span className="text-[0.68em] text-muted">{logs.data.lines_returned} lines</span>}
      </div>
      <div className="flex-1 overflow-auto p-2">
        {logs.data && logs.data.entries.length > 0 ? (
          <pre className="text-[0.7em] leading-relaxed whitespace-pre-wrap break-words font-mono">
            {logs.data.entries.map((line, i) => (
              <div key={i} className={levelClass(line)}>
                {line}
              </div>
            ))}
          </pre>
        ) : (
          <Empty>{logs.loading ? 'Loading...' : 'No log lines'}</Empty>
        )}
      </div>
    </div>
  )
}

function PlaceholderBody({ label }: { label: string }) {
  return <Empty>{label} — coming in a later phase.</Empty>
}

function BottomPanelBody({ activeTab }: { activeTab: BottomPanelTab }) {
  if (activeTab === 'logs') return <LogsBody />
  if (activeTab === 'console') return <PlaceholderBody label="Console" />
  if (activeTab === 'raw-json') return <PlaceholderBody label="Raw JSON inspector" />
  return <PlaceholderBody label="Research Notes" />
}

/**
 * Resizable panel host (institutional-redesign Phase 1) — wraps the main
 * module content and a bottom strip (Logs/Console/Raw JSON/Notes) in a
 * react-resizable-panels vertical Group. Owns all resize/collapse wiring
 * so App.tsx's Shell stays a pure JSX-layout swap: <BottomPanel>{main
 * content}</BottomPanel> replaces the old bare <main>...</main>.
 */
export function BottomPanel({ children }: { children: ReactNode }) {
  const open = useUiStore((s) => s.bottomPanelOpen)
  const setOpen = useUiStore((s) => s.setBottomPanelOpen)
  const height = useUiStore((s) => s.bottomPanelHeight)
  const setHeight = useUiStore((s) => s.setBottomPanelHeight)
  const activeTab = useUiStore((s) => s.bottomPanelTab)
  const setActiveTab = useUiStore((s) => s.setBottomPanelTab)

  const panelRef = useRef<PanelImperativeHandle>(null)

  const toggleOpen = () => {
    if (open) {
      panelRef.current?.collapse()
    } else {
      panelRef.current?.expand()
    }
    setOpen(!open)
  }

  const onResize = (size: PanelSize) => {
    if (size.inPixels > BOTTOM_PANEL_MIN_HEIGHT) setHeight(size.inPixels)
  }

  return (
    <Group orientation="vertical" className="flex-1 min-h-0 flex flex-col">
      <Panel id="main-content" minSize={200} groupResizeBehavior="preserve-relative-size" className="overflow-auto">
        {children}
      </Panel>
      <Separator className="h-1.5 bg-border hover:bg-accent/40 active:bg-accent/60 cursor-row-resize shrink-0 transition-colors" />
      <Panel
        id="bottom-panel"
        panelRef={panelRef}
        collapsible
        collapsedSize={BOTTOM_PANEL_COLLAPSED_HEIGHT}
        defaultSize={open ? height : BOTTOM_PANEL_COLLAPSED_HEIGHT}
        minSize={BOTTOM_PANEL_MIN_HEIGHT}
        maxSize="70vh"
        onResize={onResize}
        className="flex flex-col bg-panel border-t border-border"
      >
        <div className="flex items-center gap-1 px-2 shrink-0" style={{ height: BOTTOM_PANEL_COLLAPSED_HEIGHT }}>
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => {
                setActiveTab(t.id)
                if (!open) toggleOpen()
              }}
              className={`px-2.5 py-1 text-[0.72em] rounded ${
                open && activeTab === t.id ? 'bg-accent/10 text-accent' : 'text-muted hover:text-text'
              }`}
            >
              {t.label}
            </button>
          ))}
          <button onClick={toggleOpen} className="ml-auto px-2 py-1 text-[0.72em] text-muted hover:text-accent" title={open ? 'Collapse panel' : 'Expand panel'}>
            {open ? '▾ Collapse' : '▴ Expand'}
          </button>
        </div>
        {open && <div className="flex-1 min-h-0 overflow-hidden">{<BottomPanelBody activeTab={activeTab} />}</div>}
      </Panel>
    </Group>
  )
}
