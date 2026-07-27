import { apiGet, apiPostFormData } from '../../lib/api'
import {
  getResearchSymbols,
  getResearchEngines,
  getResearchIndicators,
  getResearch,
  getValidationConfig,
  getScenarioConfig,
  askResearchQuestion,
  type SymbolsResponse,
  type SymbolEntry,
  type EnginesResponse,
  type EngineEntry,
  type IndicatorsResponse,
  type IndicatorEntry,
  type ResearchResponse,
  type Hypothesis,
  type ValidationConfigResponse,
  type ScenarioConfigResponse,
  type ScenarioField,
  type AiResearchAnswer,
} from '../research-backtests/api'
import {
  getJobCatalog, runJob, getJobDetail, runRobustnessJob, getJobList,
  type JobCatalogResponse, type JobDescriptor, type JobSummary, type JobDetail, type JobListResponse,
} from '../experiment-runner/api'

// Backtesting Lab (Research Workbench, 2026-07-25) reuses the SAME
// endpoints the Research & Backtests monitoring tab already calls — this
// module is a different UI shape (wizard vs. dashboard) over identical
// data, not a second backend integration. Only /research/datasets has no
// existing frontend consumer yet.
export {
  getResearchSymbols,
  getResearchEngines,
  getResearchIndicators,
  getResearch,
  getValidationConfig,
  getScenarioConfig,
  askResearchQuestion,
  getJobCatalog,
  runJob,
  runRobustnessJob,
  getJobDetail,
  getJobList,
}
export type {
  SymbolsResponse,
  SymbolEntry,
  EnginesResponse,
  EngineEntry,
  IndicatorsResponse,
  IndicatorEntry,
  ResearchResponse,
  Hypothesis,
  ValidationConfigResponse,
  ScenarioConfigResponse,
  ScenarioField,
  AiResearchAnswer,
  JobCatalogResponse,
  JobDescriptor,
  JobSummary,
  JobDetail,
  JobListResponse,
}

// Dataset Explorer (Phase 3 backend, 2026-07-24) — local historical CSVs
// actually available to backtest against, with real date ranges (reused
// from backtest.runner's exact read path, not a guess).
export interface DatasetEntry {
  symbol: string
  timeframe: string
  file: string
  size_bytes: number
  known_symbol: boolean
  readable: boolean
  rows?: number
  start?: string | null
  end?: string | null
  error?: string
}

export interface DatasetsResponse {
  data_dir: string
  count: number
  datasets: DatasetEntry[]
}

export const getResearchDatasets = () => apiGet<DatasetsResponse>('/research/datasets')

// Dataset upload (Phase 4a, 2026-07-25) — CSV/Parquet import, server-
// constructed filename (data/{SYMBOL}_{TIMEFRAME}_uploaded.{csv|parquet}),
// content-sniffed format, validated via core.data_validator.validate_ohlcv.
export interface UploadDatasetResponse {
  symbol: string
  timeframe: string
  file: string
  rows: number
  start: string | null
  end: string | null
}

export const uploadDataset = (form: FormData) => apiPostFormData<UploadDatasetResponse>('/research/datasets/upload', form)
