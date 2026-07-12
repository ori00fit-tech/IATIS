import { useState } from 'react'
import { Panel, Empty } from '../../components/Panel'
import { REPORT_KINDS, getReportJson, reportDownloadUrl, type ReportJsonResponse } from './api'

export function Reports() {
  const [viewing, setViewing] = useState<string | null>(null)
  const [data, setData] = useState<ReportJsonResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const view = async (kind: string) => {
    setViewing(kind)
    setLoading(true)
    setError(null)
    setData(null)
    try {
      setData(await getReportJson(kind))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Reports" right="Markdown or JSON — read-only snapshots">
        <div className="divide-y divide-border">
          {REPORT_KINDS.map((k) => (
            <div key={k.id} className="px-4 py-3 flex items-center justify-between gap-4">
              <div>
                <div className="text-[0.85em] font-bold text-text">{k.title}</div>
                <div className="text-[0.78em] text-muted">{k.description}</div>
              </div>
              <div className="flex gap-3 shrink-0">
                <button onClick={() => view(k.id)} className="text-accent hover:text-accent2 text-[0.82em] underline decoration-dotted">
                  View JSON
                </button>
                <a href={reportDownloadUrl(k.id)} className="text-accent hover:text-accent2 text-[0.82em] underline decoration-dotted">
                  Download .md
                </a>
              </div>
            </div>
          ))}
        </div>
      </Panel>

      {viewing && (
        <Panel
          title={data?.title ?? viewing}
          right={<button onClick={() => setViewing(null)} className="text-muted hover:text-text">✕ close</button>}
        >
          {loading ? (
            <Empty>Loading...</Empty>
          ) : error ? (
            <Empty>Failed: {error}</Empty>
          ) : (
            <pre className="p-4 text-[0.78em] overflow-auto max-h-[600px] whitespace-pre-wrap break-words">
              {JSON.stringify(data?.data, null, 2)}
            </pre>
          )}
        </Panel>
      )}
    </div>
  )
}
