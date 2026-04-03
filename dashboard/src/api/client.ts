import axios from 'axios'
import type { TestRun, CoverageReport, FlakinessScore, AgentStatus } from '../types'

const http = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? 'http://localhost:8000',
})

export const getRuns = (): Promise<TestRun[]> =>
  http.get<TestRun[]>('/api/runs').then((r) => r.data)

export const getRun = (id: string): Promise<TestRun> =>
  http.get<TestRun>(`/api/runs/${id}`).then((r) => r.data)

export const getCoverage = (): Promise<CoverageReport[]> =>
  http.get<CoverageReport[]>('/api/coverage').then((r) => r.data)

export const getFlakiness = (): Promise<FlakinessScore[]> =>
  http.get<FlakinessScore[]>('/api/flakiness').then((r) => r.data)

export const getAgentStatus = (): Promise<AgentStatus[]> =>
  http.get<AgentStatus[]>('/api/agents/status').then((r) => r.data)

export const triggerRun = (prNumber: number): Promise<{ job_id: string }> =>
  http.post<{ job_id: string }>('/api/trigger', { pr_number: prNumber }).then((r) => r.data)

export const syncPRs = (repo?: string): Promise<{ job_id: string }[]> =>
  http.post<{ job_id: string }[]>('/api/runs/sync', null, { params: repo ? { repo } : {} }).then((r) => r.data)

export const getPRs = (repo: string, state = 'open'): Promise<import('../types').PullRequest[]> =>
  http.get<import('../types').PullRequest[]>('/api/pulls', { params: { repo, state } }).then((r) => r.data)

export const triggerPR = (pr: import('../types').PullRequest, repo: string): Promise<{ job_id: string }> =>
  http.post<{ job_id: string }>('/api/trigger', {
    pr_number:    pr.number,
    branch:       pr.branch,
    commit_sha:   pr.commit_sha,
    repo,
    changed_files: [],
  }).then((r) => r.data)

export const restartRun = (id: string): Promise<{ job_id: string }> =>
  http.post<{ job_id: string }>(`/api/runs/${id}/restart`).then((r) => r.data)

export const deleteRun = (id: string): Promise<void> =>
  http.delete(`/api/runs/${id}`).then(() => undefined)

export const getLocalBranches = (repoPath = '.'): Promise<string[]> =>
  http.get<string[]>('/api/branches/local', { params: { repo_path: repoPath } }).then((r) => r.data)

export const getCurrentBranch = (repoPath = '.'): Promise<{ branch: string }> =>
  http.get<{ branch: string }>('/api/branches/current', { params: { repo_path: repoPath } }).then((r) => r.data)

export const getRemoteBranches = (repo: string): Promise<string[]> =>
  http.get<string[]>('/api/branches/remote', { params: { repo } }).then((r) => r.data)
