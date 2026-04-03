import { useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, ExternalLink, Radio, Trash2 } from 'lucide-react'
import type { TestRun } from '../types'
import { getRun, deleteRun } from '../api/client'
import Markdown from './Markdown'

interface Props {
  runs: TestRun[]
  limit?: number
  onDelete?: (id: string) => void
}

const statusBadge: Record<TestRun['status'], string> = {
  passed: 'bg-green-400/10 text-green-400',
  failed: 'bg-red-500/10 text-red-400',
  running: 'bg-yellow-400/10 text-yellow-400',
  error: 'bg-orange-500/10 text-orange-400',
}

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  return `${Math.floor(mins / 60)}h ago`
}

const GITHUB_REPO = import.meta.env.VITE_GITHUB_REPO as string | undefined
const JIRA_URL = import.meta.env.VITE_JIRA_URL as string | undefined

function RunDetail({ runId }: { runId: string }) {
  const { data: run, isLoading } = useQuery({
    queryKey: ['run', runId],
    queryFn: () => getRun(runId),
  })

  if (isLoading) return <p className="text-gray-500 text-xs py-2">Loading…</p>
  if (!run) return null

  return (
    <div className="space-y-4 py-3">

      {/* Jira ticket */}
      {run.jira_task_id && (
        <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-semibold text-blue-400 uppercase tracking-wider">Jira ticket</span>
            {JIRA_URL && (
              <a
                href={`${JIRA_URL}/browse/${run.jira_task_id}`}
                target="_blank"
                rel="noreferrer"
                className="text-blue-400 hover:text-blue-300"
              >
                <ExternalLink size={12} />
              </a>
            )}
          </div>
          <span className="font-mono text-sm text-blue-300">{run.jira_task_id}</span>
        </div>
      )}

      {/* Error */}
      {run.error_message && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-3">
          <p className="text-xs font-semibold text-red-400 uppercase tracking-wider mb-1">Pipeline error</p>
          <pre className="text-xs text-red-300 whitespace-pre-wrap break-all">{run.error_message}</pre>
        </div>
      )}

      {/* Generated tests */}
      {run.generated_tests && run.generated_tests.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Generated tests ({run.generated_tests.length})
          </p>
          <ul className="space-y-1">
            {run.generated_tests.map((path) => (
              <li key={path} className="font-mono text-xs text-gray-300 bg-gray-900 rounded px-2 py-1">{path}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Debug results */}
      {run.debug_results && run.debug_results.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Debug analysis ({run.debug_results.length} failure{run.debug_results.length > 1 ? 's' : ''})
          </p>
          <div className="space-y-3">
            {run.debug_results.map((d, i) => (
              <div key={i} className="bg-gray-900 rounded-lg p-3 border border-gray-700 space-y-2">
                <p className="font-mono text-xs text-yellow-400">{d.test_name}</p>
                <div>
                  <span className="text-xs text-gray-500">Root cause: </span>
                  <span className="text-xs text-gray-200">{d.root_cause}</span>
                </div>
                <div>
                  <span className="text-xs text-gray-500">Fix: </span>
                  <span className="text-xs text-gray-200">{d.fix_suggestion}</span>
                </div>
                {d.fix_code && (
                  <pre className="text-xs bg-gray-800 rounded p-2 text-green-300 overflow-x-auto">{d.fix_code}</pre>
                )}
                {d.confidence != null && (
                  <span className="text-xs text-gray-500">Confidence: {d.confidence}%</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Report */}
      {run.report && (
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Report</p>
          <div className="bg-gray-900 rounded-lg p-3 border border-gray-700">
            <Markdown className="text-xs">{run.report}</Markdown>
          </div>
        </div>
      )}

      {!run.jira_task_id && !run.error_message && !run.generated_tests?.length && !run.report && (
        <p className="text-xs text-gray-600 italic">No details available yet.</p>
      )}
    </div>
  )
}

export default function TestRunTable({ runs, limit, onDelete }: Props) {
  const displayed = limit ? runs.slice(0, limit) : runs
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation()
    if (!window.confirm('Delete this run from the database?')) return
    setDeletingId(id)
    try {
      await deleteRun(id)
      onDelete?.(id)
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-500 uppercase tracking-wider border-b border-gray-700">
            <th className="pb-3 pr-2 w-4" />
            <th className="pb-3 pr-4">PR</th>
            <th className="pb-3 pr-4">Jira</th>
            <th className="pb-3 pr-4">Branch</th>
            <th className="pb-3 pr-4">Status</th>
            <th className="pb-3 pr-4">Passed</th>
            <th className="pb-3 pr-4">Failed</th>
            <th className="pb-3 pr-4">Duration</th>
            <th className="pb-3 pr-4">When</th>
            <th className="pb-3" />
          </tr>
        </thead>
        <tbody>
          {displayed.map((run) => {
            const isExpanded = expandedId === run.id
            return (
              <>
                <tr
                  key={run.id}
                  onClick={() => setExpandedId(isExpanded ? null : run.id)}
                  className="border-b border-gray-700/50 hover:bg-gray-700/30 transition-colors cursor-pointer"
                >
                  <td className="py-3 pr-2 text-gray-500">
                    {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </td>
                  <td className="py-3 pr-4 font-mono text-indigo-400">
                    {(run.repo || GITHUB_REPO) ? (
                      <a
                        href={`https://github.com/${run.repo || GITHUB_REPO}/pull/${run.pr_number}`}
                        target="_blank"
                        rel="noreferrer"
                        className="hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        #{run.pr_number}
                      </a>
                    ) : `#${run.pr_number}`}
                  </td>
                  <td className="py-3 pr-4">
                    {run.jira_task_id ? (
                      <a
                        href={JIRA_URL ? `${JIRA_URL}/browse/${run.jira_task_id}` : '#'}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-blue-400 font-mono text-xs hover:underline"
                      >
                        {run.jira_task_id}
                      </a>
                    ) : (
                      <span className="text-gray-600 text-xs">—</span>
                    )}
                  </td>
                  <td className="py-3 pr-4 text-gray-300 font-mono text-xs truncate max-w-[160px]">{run.branch}</td>
                  <td className="py-3 pr-4">
                    <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', statusBadge[run.status])}>
                      {run.status}
                    </span>
                  </td>
                  <td className="py-3 pr-4 text-green-400">{run.passed}</td>
                  <td className="py-3 pr-4 text-red-400">{run.failed}</td>
                  <td className="py-3 pr-4 text-gray-400">
                    {run.duration_seconds > 0 ? `${run.duration_seconds.toFixed(1)}s` : '—'}
                  </td>
                  <td className="py-3 text-gray-500">{timeAgo(run.created_at)}</td>
                  <td className="py-3 pl-2">
                    <div className="flex items-center gap-1">
                      <Link
                        to={`/runs/${run.id}`}
                        onClick={(e) => e.stopPropagation()}
                        className={clsx(
                          'flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium transition-colors',
                          run.status === 'running'
                            ? 'bg-yellow-400/10 text-yellow-400 hover:bg-yellow-400/20'
                            : 'bg-gray-700/60 text-gray-500 hover:bg-gray-700',
                        )}
                        title="Open live view"
                      >
                        <Radio size={10} className={run.status === 'running' ? 'animate-pulse' : ''} />
                        Live
                      </Link>
                      <button
                        onClick={(e) => handleDelete(e, run.id)}
                        disabled={deletingId === run.id}
                        title="Delete run"
                        className="flex items-center px-1.5 py-0.5 rounded text-xs text-gray-600 hover:text-red-400 hover:bg-red-500/10 disabled:opacity-40 transition-colors"
                      >
                        <Trash2 size={12} className={deletingId === run.id ? 'animate-pulse' : ''} />
                      </button>
                    </div>
                  </td>
                </tr>
                {isExpanded && (
                  <tr key={`${run.id}-detail`} className="border-b border-gray-700/50 bg-gray-900/40">
                    <td colSpan={10} className="px-6">
                      <RunDetail runId={run.id} />
                    </td>
                  </tr>
                )}
              </>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
