import { useEffect, useState } from 'react'
import { useAuth } from '../../lib/auth'
import { ApiError } from '../../lib/api'
import { Panel, Empty } from '../../components/Panel'
import {
  getFilesTree,
  getFileContent,
  getFileDiff,
  searchFiles,
  downloadUrl,
  type FileEntry,
  type FileContentResponse,
  type FileDiffResponse,
  type FileSearchResult,
} from './api'

const input = 'bg-surface border border-border rounded px-2 py-1.5 text-[0.82em] text-text placeholder:text-muted'

function formatSize(size: number | null) {
  if (size == null) return '—'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

export function FileExplorer() {
  const { markUnauthenticated } = useAuth()
  const [path, setPath] = useState('')
  const [entries, setEntries] = useState<FileEntry[]>([])
  const [treeError, setTreeError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [content, setContent] = useState<FileContentResponse | null>(null)
  const [diff, setDiff] = useState<FileDiffResponse | null>(null)
  const [showDiff, setShowDiff] = useState(false)
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<FileSearchResult[] | null>(null)

  const handleAuthError = (err: unknown) => {
    if (err instanceof ApiError && err.status === 401) {
      markUnauthenticated()
      return true
    }
    return false
  }

  const loadTree = (p: string) => {
    getFilesTree(p)
      .then((r) => {
        setEntries(r.entries)
        setTreeError(null)
        setPath(r.path)
        setSearchResults(null)
      })
      .catch((err) => {
        if (handleAuthError(err)) return
        setTreeError(err instanceof Error ? err.message : String(err))
      })
  }

  useEffect(() => {
    loadTree('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openFile = (p: string) => {
    setSelected(p)
    setShowDiff(false)
    setDiff(null)
    getFileContent(p)
      .then(setContent)
      .catch((err) => {
        if (handleAuthError(err)) return
        setContent({ path: p, size: 0, binary: false, truncated: false, content: null, error: err instanceof Error ? err.message : String(err) })
      })
  }

  const toggleDiff = () => {
    if (!selected) return
    if (showDiff) {
      setShowDiff(false)
      return
    }
    getFileDiff(selected)
      .then((d) => {
        setDiff(d)
        setShowDiff(true)
      })
      .catch((err) => handleAuthError(err))
  }

  const runSearch = () => {
    if (!query.trim()) {
      setSearchResults(null)
      return
    }
    searchFiles(query, path)
      .then((r) => setSearchResults(r.results))
      .catch((err) => handleAuthError(err))
  }

  const segments = path ? path.split('/') : []

  return (
    <div className="flex flex-col gap-4">
      <Panel title="File Explorer" right="read-only">
        <div className="flex flex-wrap items-end gap-2 px-4 py-3 border-b border-border bg-surface/40">
          <div className="flex items-center gap-1 text-[0.82em] flex-wrap">
            <button onClick={() => loadTree('')} className="text-accent hover:text-accent2">
              root
            </button>
            {segments.map((seg, i) => (
              <span key={i} className="flex items-center gap-1">
                <span className="text-muted">/</span>
                <button onClick={() => loadTree(segments.slice(0, i + 1).join('/'))} className="text-accent hover:text-accent2">
                  {seg}
                </button>
              </span>
            ))}
          </div>
          <div className="flex-1" />
          <label className="flex flex-col gap-1 min-w-[220px]">
            <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">Search (filenames + content)</span>
            <input
              className={input}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && runSearch()}
              placeholder="search under current path..."
            />
          </label>
          <div className="flex gap-2 pb-0.5">
            <button onClick={runSearch} className="px-3 py-1.5 text-[0.78em] rounded bg-accent/15 text-accent hover:bg-accent/25">
              Search
            </button>
            {searchResults && (
              <button
                onClick={() => {
                  setSearchResults(null)
                  setQuery('')
                }}
                className="px-3 py-1.5 text-[0.78em] rounded text-muted hover:text-text"
              >
                Clear
              </button>
            )}
          </div>
        </div>

        <div className="grid grid-cols-[minmax(220px,320px)_1fr] divide-x divide-border">
          <div className="max-h-[520px] overflow-y-auto">
            {searchResults ? (
              searchResults.length > 0 ? (
                searchResults.map((res, i) => (
                  <button
                    key={i}
                    onClick={() => openFile(res.path)}
                    className="w-full text-left px-3 py-2 text-[0.78em] border-b border-border hover:bg-accent/[0.05] block"
                  >
                    <div className="text-accent truncate">{res.path}</div>
                    {res.match_type === 'content' && (
                      <div className="text-muted truncate">
                        L{res.line}: {res.snippet}
                      </div>
                    )}
                  </button>
                ))
              ) : (
                <Empty>No matches</Empty>
              )
            ) : treeError ? (
              <div className="p-4 text-[0.82em] text-red">{treeError}</div>
            ) : entries.length > 0 ? (
              entries.map((e) => (
                <button
                  key={e.path}
                  onClick={() => (e.type === 'dir' ? loadTree(e.path) : openFile(e.path))}
                  className={`w-full text-left px-3 py-2 text-[0.82em] border-b border-border hover:bg-accent/[0.05] flex items-center justify-between ${
                    selected === e.path ? 'bg-accent/[0.08]' : ''
                  }`}
                >
                  <span className={e.type === 'dir' ? 'text-accent' : 'text-text'}>
                    {e.type === 'dir' ? '📁' : '📄'} {e.name}
                  </span>
                  <span className="text-muted text-[0.75em]">{formatSize(e.size)}</span>
                </button>
              ))
            ) : (
              <Empty>Empty directory</Empty>
            )}
          </div>

          <div className="max-h-[520px] overflow-y-auto">
            {selected ? (
              <div className="flex flex-col">
                <div className="flex items-center justify-between px-4 py-2 border-b border-border sticky top-0 bg-card">
                  <span className="text-[0.8em] text-text truncate">{selected}</span>
                  <div className="flex gap-3 text-[0.78em] shrink-0">
                    <button onClick={toggleDiff} className="text-accent hover:text-accent2">
                      {showDiff ? 'Hide diff' : 'Diff vs HEAD'}
                    </button>
                    <a href={downloadUrl(selected)} className="text-accent hover:text-accent2">
                      Download
                    </a>
                  </div>
                </div>
                {showDiff && diff && (
                  <pre className="p-3 text-[0.75em] whitespace-pre-wrap break-words border-b border-border bg-surface/60">
                    {diff.has_changes ? diff.diff : 'No uncommitted changes vs HEAD.'}
                  </pre>
                )}
                {content?.error ? (
                  <div className="p-4 text-[0.82em] text-amber">{content.error}</div>
                ) : (
                  <pre className="p-4 text-[0.78em] whitespace-pre-wrap break-words font-mono">{content?.content}</pre>
                )}
              </div>
            ) : (
              <Empty>Select a file to view it</Empty>
            )}
          </div>
        </div>
      </Panel>
    </div>
  )
}
