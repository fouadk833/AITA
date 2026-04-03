import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useRuns } from '../hooks/useTestData'
import TestRunTable from '../components/TestRunTable'
import { RefreshCw, GitPullRequest, Play, ChevronDown, ChevronUp, ExternalLink } from 'lucide-react'
import { getPRs, triggerPR } from '../api/client'
import type { PullRequest } from '../types'

const DEFAULT_REPO = (import.meta.env.VITE_GITHUB_REPO as string | undefined) ?? ''

export default function TestRuns() {
  const { data: runs = [], isFetching, refetch } = useRuns()
  const navigate = useNavigate()

  const [targetRepo, setTargetRepo]   = useState(DEFAULT_REPO)
  const [prs, setPRs]                 = useState<PullRequest[]>([])
  const [prsOpen, setPrsOpen]         = useState(false)
  const [loadingPRs, setLoadingPRs]   = useState(false)
  const [prError, setPrError]         = useState<string | null>(null)
  const [triggering, setTriggering]   = useState<number | null>(null)   // PR number being triggered

  async function handleListPRs() {
    if (!targetRepo.trim()) return
    setLoadingPRs(true)
    setPrError(null)
    setPRs([])
    try {
      const result = await getPRs(targetRepo.trim())
      setPRs(result)
      setPrsOpen(true)
      if (result.length === 0) setPrError('No open PRs found.')
    } catch (err: any) {
      setPrError(err?.response?.data?.detail ?? 'Failed to fetch PRs.')
    } finally {
      setLoadingPRs(false)
    }
  }

  async function handleRun(pr: PullRequest) {
    setTriggering(pr.number)
    try {
      const { job_id } = await triggerPR(pr, targetRepo.trim())
      await refetch()
      navigate(`/runs/${job_id}`)
    } catch (err: any) {
      setPrError(err?.response?.data?.detail ?? `Failed to trigger PR #${pr.number}.`)
    } finally {
      setTriggering(null)
    }
  }

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">Test Runs</h1>
          <p className="text-gray-400 text-sm mt-1">{runs.length} total runs</p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={targetRepo}
            onChange={(e) => setTargetRepo(e.target.value)}
            placeholder="owner/repo"
            className="px-3 py-2 rounded-lg bg-gray-900 border border-gray-700 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-indigo-500 w-48 font-mono"
          />
          <button
            onClick={handleListPRs}
            disabled={loadingPRs || !targetRepo.trim()}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-indigo-600 border border-indigo-500 text-sm text-white hover:bg-indigo-500 disabled:opacity-50 transition-colors"
          >
            <GitPullRequest size={14} className={loadingPRs ? 'animate-pulse' : ''} />
            {loadingPRs ? 'Loading…' : 'List PRs'}
          </button>
          <button
            onClick={() => refetch()}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-sm text-gray-300 hover:bg-gray-700 transition-colors"
          >
            <RefreshCw size={14} className={isFetching ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Error */}
      {prError && (
        <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-2">
          {prError}
        </div>
      )}

      {/* PR list — collapsed/expanded */}
      {prs.length > 0 && (
        <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
          <button
            onClick={() => setPrsOpen((v) => !v)}
            className="w-full flex items-center justify-between px-5 py-3 text-sm font-medium text-gray-300 hover:bg-gray-700/40 transition-colors"
          >
            <span className="flex items-center gap-2">
              <GitPullRequest size={14} className="text-indigo-400" />
              Open PRs ({prs.length}) — click <span className="text-white font-semibold">Run</span> to start tests
            </span>
            {prsOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>

          {prsOpen && (
            <div className="divide-y divide-gray-700/60">
              {prs.map((pr) => (
                <div key={pr.number} className="flex items-start justify-between px-5 py-4 hover:bg-gray-700/20 transition-colors">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-indigo-400">#{pr.number}</span>
                      {pr.draft && (
                        <span className="px-1.5 py-0.5 rounded text-xs bg-gray-700 text-gray-400">Draft</span>
                      )}
                      <a
                        href={pr.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-sm text-gray-200 hover:text-white font-medium truncate flex items-center gap-1"
                      >
                        {pr.title}
                        <ExternalLink size={10} className="flex-shrink-0 text-gray-500" />
                      </a>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-gray-500">
                      <span className="font-mono">{pr.branch}</span>
                      <span>by {pr.author}</span>
                      <span>{pr.changed_files} file{pr.changed_files !== 1 ? 's' : ''} changed</span>
                      <span className="text-green-500">+{pr.additions}</span>
                      <span className="text-red-500">-{pr.deletions}</span>
                    </div>
                  </div>
                  <button
                    onClick={() => handleRun(pr)}
                    disabled={triggering !== null}
                    className="ml-4 flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-600/20 border border-green-600/40 text-green-400 text-xs font-semibold hover:bg-green-600/30 disabled:opacity-40 transition-colors"
                  >
                    <Play size={11} className={triggering === pr.number ? 'animate-pulse' : ''} />
                    {triggering === pr.number ? 'Starting…' : 'Run Tests'}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Run history */}
      <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <TestRunTable runs={runs} onDelete={() => refetch()} />
      </div>
    </div>
  )
}
