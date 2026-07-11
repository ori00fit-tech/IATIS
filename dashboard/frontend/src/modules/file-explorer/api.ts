import { apiGet } from '../../lib/api'

export interface FileEntry {
  name: string
  path: string
  type: 'file' | 'dir'
  size: number | null
  modified: string
}

export interface FilesTreeResponse {
  path: string
  entries: FileEntry[]
}

export interface FileContentResponse {
  path: string
  size: number
  binary: boolean
  truncated: boolean
  content: string | null
  error: string | null
}

export interface FileDiffResponse {
  path: string
  diff: string
  has_changes: boolean
  error: string | null
}

export interface FileSearchResult {
  path: string
  match_type: 'filename' | 'content'
  line: number | null
  snippet: string
}

export interface FilesSearchResponse {
  query: string
  path: string
  results: FileSearchResult[]
  truncated: boolean
}

export const getFilesTree = (path = '') => apiGet<FilesTreeResponse>('/files/tree', { path })
export const getFileContent = (path: string) => apiGet<FileContentResponse>('/files/read', { path })
export const getFileDiff = (path: string) => apiGet<FileDiffResponse>('/files/diff', { path })
export const searchFiles = (query: string, path = '') => apiGet<FilesSearchResponse>('/files/search', { query, path })
export const downloadUrl = (path: string) => `/files/download?path=${encodeURIComponent(path)}`
