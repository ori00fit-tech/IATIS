import { apiGet, apiPost } from '../../lib/api'

export interface JobDescriptor {
  id: string
  description: string
  category: 'research' | 'ops'
  /** True for jobs (backtest) that reject a run without symbols. */
  requires_symbols?: boolean
}

export interface JobCatalogResponse {
  jobs: JobDescriptor[]
}

export type JobStatus = 'queued' | 'running' | 'finished' | 'failed' | 'timeout'

export interface JobSummary {
  job_id: string
  job: string
  status: JobStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  returncode: number | null
  log_lines: number
}

export interface JobDetail extends JobSummary {
  log: string[]
}

export interface JobListResponse {
  jobs: JobSummary[]
}

export const getJobCatalog = () => apiGet<JobCatalogResponse>('/experiments/jobs')
export const getJobList = () => apiGet<JobListResponse>('/experiments')
export const getJobDetail = (jobId: string) => apiGet<JobDetail>(`/experiments/${jobId}`)
export const runJob = (job: string, symbols?: string[]) => apiPost<JobSummary>('/experiments/run', { job, symbols })
