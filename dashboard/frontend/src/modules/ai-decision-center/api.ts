// AI Decision Center is an explain-only surface (VISION_v2): it reads the
// decision feed and, on demand, asks the AI layer to phrase a past decision
// in plain English. It never generates, alters, or prescribes a signal — the
// AI explanation endpoint is cached and derived from a decision already made.
export {
  getDecisions,
  explainTrade,
  type DecisionEntry,
  type DecisionsResponse,
  type PipelineReport,
  type TradeExplanation,
} from '../live-signals/api'
