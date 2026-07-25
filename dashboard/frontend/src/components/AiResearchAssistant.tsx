import { useState } from 'react'
import { Panel } from './Panel'
import { AiStatusFrame } from './AiStatusFrame'
import { askResearchQuestion, type AiResearchAnswer } from '../modules/research-backtests/api'

/**
 * AI Research Assistant (Backtesting Lab priority #5) — a grounded Q&A
 * panel over whatever research/backtest data the calling view already
 * has in memory. `context` IS the entire evidence sent to the model
 * (POST /ai/research-question forwards it verbatim, see
 * ai/prompts/research_qa.txt) — this component never fetches anything
 * of its own, so a question can only ever be answered from data the
 * operator is already looking at on screen.
 */
export function AiResearchAssistant({
  context,
  examples,
  emptyHint,
}: {
  context: unknown
  examples?: string[]
  emptyHint?: string
}) {
  const [question, setQuestion] = useState('')
  const [asked, setAsked] = useState<{ loading: boolean; error: string | null; data: AiResearchAnswer | null; question: string } | null>(null)

  const hasContext = context != null && (typeof context !== 'object' || Object.keys(context as object).length > 0)

  const ask = (q: string) => {
    const trimmed = q.trim()
    if (!trimmed || !hasContext) return
    setAsked({ loading: true, error: null, data: null, question: trimmed })
    askResearchQuestion(context, trimmed)
      .then((data) => setAsked({ loading: false, error: null, data, question: trimmed }))
      .catch((err) => setAsked({ loading: false, error: err instanceof Error ? err.message : String(err), data: null, question: trimmed }))
  }

  return (
    <Panel title="AI Research Assistant">
      <div className="p-4 flex flex-col gap-3">
        {!hasContext ? (
          <p className="text-[0.85em] text-muted">{emptyHint ?? 'No data loaded yet — nothing to ask about.'}</p>
        ) : (
          <>
            {examples && examples.length > 0 && (
              <div className="flex items-center gap-2 flex-wrap">
                {examples.map((ex) => (
                  <button
                    key={ex}
                    onClick={() => {
                      setQuestion(ex)
                      ask(ex)
                    }}
                    disabled={asked?.loading}
                    className="px-2.5 py-1.5 text-[0.75em] rounded border border-border text-muted hover:text-accent hover:border-accent disabled:opacity-50"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            )}
            <form
              onSubmit={(e) => {
                e.preventDefault()
                ask(question)
              }}
              className="flex items-center gap-2"
            >
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Ask about the data currently on screen…"
                maxLength={500}
                className="flex-1 px-3 py-1.5 text-[0.82em] rounded border border-border bg-bg text-text font-mono"
              />
              <button
                type="submit"
                disabled={asked?.loading || !question.trim()}
                className="px-4 py-1.5 text-[0.82em] rounded border border-accent text-accent hover:bg-accent/10 disabled:opacity-40 font-bold"
              >
                {asked?.loading ? 'Asking…' : 'Ask'}
              </button>
            </form>
            {asked && (
              <div className="border border-border rounded-lg p-3 bg-surface/40">
                <p className="text-[0.75em] text-muted mb-2">{asked.question}</p>
                <AiStatusFrame loading={asked.loading} fetchError={asked.error} status={asked.data?.status} providerError={asked.data?.error}>
                  <p className="text-[0.88em] whitespace-pre-wrap">{asked.data?.text}</p>
                </AiStatusFrame>
              </div>
            )}
          </>
        )}
      </div>
    </Panel>
  )
}
