import { useEffect, useRef, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { ArrowLeft, RefreshCw, Terminal, ChevronDown, ChevronRight, CheckCircle2, XCircle } from 'lucide-react'
import { useRunWebSocket } from '../hooks/useRunWebSocket'
import { getRun, restartRun } from '../api/client'
import Markdown from '../components/Markdown'
import type { TestRun } from '../types'

// ------------------------------------------------------------------ types
interface NodeStatus { status: 'pending' | 'running' | 'done' | 'error'; message?: string }
interface RunResult  { passed: number; failed: number; skipped: number; duration: number }
interface DebugEntry { test_name: string; root_cause: string; fix_suggestion: string; confidence: number }

interface ConsoleLog {
  source:   string
  stdout:   string
  stderr:   string
  passed:   number
  failed:   number
  skipped:  number
  exit_code: number
}

interface CachedRunUIState {
  nodes:          Record<string, NodeStatus>
  llmStreams:     Record<string, string>
  activeAgent:    string | null
  savedTests:     string[]
  runResult:      RunResult | null
  debugEntries:   DebugEntry[]
  consoleLogs:    ConsoleLog[]
  report:         string
  finalStatus:    string | null
  eventsProcessed: number
}

// Module-level UI state cache — survives navigation within the same page session
const _runUICache = new Map<string, CachedRunUIState>()

// ------------------------------------------------------------------ config
const PIPELINE_NODES = [
  { id: 'fetch_jira',           label: 'Fetch Jira' },
  { id: 'analyze',              label: 'Analyze Changes' },
  { id: 'clone_repo',           label: 'Clone Repository' },
  { id: 'setup_workspace',      label: 'Setup Workspace' },
  { id: 'generate_unit',        label: 'Generate Unit Tests' },
  { id: 'generate_integration', label: 'Generate Integration Tests' },
  { id: 'generate_e2e',         label: 'Generate E2E Tests' },
  { id: 'run_tests',            label: 'Run Tests' },
  { id: 'debug',                label: 'Debug Failures' },
  { id: 'reporter',             label: 'Build Report' },
  { id: 'cleanup',              label: 'Cleanup' },
]

const FINAL_STATUSES = new Set(['passed', 'failed', 'error'])

const STATUS_BADGE: Record<string, string> = {
  passed: 'bg-green-400/10 text-green-400',
  failed: 'bg-red-500/10 text-red-400',
  error:  'bg-orange-500/10 text-orange-400',
  running:'bg-yellow-400/10 text-yellow-400',
}

// ------------------------------------------------------------------ helpers
function seedFromRun(
  run: TestRun,
  setNodes:        React.Dispatch<React.SetStateAction<Record<string, NodeStatus>>>,
  setRunResult:    React.Dispatch<React.SetStateAction<RunResult | null>>,
  setDebugEntries: React.Dispatch<React.SetStateAction<DebugEntry[]>>,
  setConsoleLogs:  React.Dispatch<React.SetStateAction<ConsoleLog[]>>,
  setReport:       React.Dispatch<React.SetStateAction<string>>,
  setSavedTests:   React.Dispatch<React.SetStateAction<string[]>>,
) {
  // If run is finished, mark all nodes done (we don't know exact per-node status from DB)
  if (FINAL_STATUSES.has(run.status)) {
    setNodes(
      Object.fromEntries(PIPELINE_NODES.map((n) => [n.id, { status: 'done' as const }]))
    )
  }
  if (run.passed || run.failed || run.skipped || run.duration_seconds) {
    setRunResult({
      passed:   run.passed,
      failed:   run.failed,
      skipped:  run.skipped,
      duration: run.duration_seconds,
    })
  }
  if (run.debug_results?.length) {
    setDebugEntries(
      run.debug_results.map((d) => ({
        test_name:      d.test_name,
        root_cause:     d.root_cause,
        fix_suggestion: d.fix_suggestion,
        confidence:     d.confidence ?? 0,
      }))
    )
  }
  if (run.console_output?.length) setConsoleLogs(run.console_output as ConsoleLog[])
  if (run.report) setReport(run.report)
  if (run.generated_tests?.length) setSavedTests(run.generated_tests)
}

// ------------------------------------------------------------------ TerminalOutput
// Colorises pytest / jest console lines without needing a markdown parser.
function TerminalOutput({ text, className }: { text: string; className?: string }) {
  if (!text.trim()) return null
  return (
    <div className={className}>
      {text.split('\n').map((line, i) => {
        const l = line
        let color = 'text-gray-300'
        if (/\bPASSED\b/.test(l))                            color = 'text-green-400'
        else if (/\bFAILED\b|\bFAILURE\b/.test(l))          color = 'text-red-400'
        else if (/\bERROR\b/.test(l))                        color = 'text-orange-400'
        else if (/\bWARNING\b|\bWARN\b/i.test(l))           color = 'text-yellow-400'
        else if (/\bSKIPPED\b/.test(l))                      color = 'text-yellow-300'
        else if (/^={3,}|^-{3,}/.test(l.trim()))            color = 'text-gray-500'
        else if (/^\s+at\s|^\s+File "/.test(l))             color = 'text-gray-400'
        else if (/\d+ passed/.test(l))                       color = 'text-green-400'
        else if (/\d+ failed/.test(l))                       color = 'text-red-400'
        return (
          <div key={i} className={`${color} leading-5 font-mono text-xs whitespace-pre-wrap break-all`}>
            {line || '\u00A0'}
          </div>
        )
      })}
    </div>
  )
}

// ------------------------------------------------------------------ DevConsole
function DevConsole({ logs }: { logs: ConsoleLog[] }) {
  const [open, setOpen] = useState(true)
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})
  const consoleRef = useRef<HTMLDivElement>(null)

  // Auto-scroll when new logs arrive
  useEffect(() => {
    if (open && consoleRef.current)
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight
  }, [logs.length, open])

  function toggle(i: number) {
    setExpanded((prev) => ({ ...prev, [i]: !prev[i] }))
  }

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-700 overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-5 py-3 hover:bg-gray-800/60 transition-colors"
      >
        <span className="flex items-center gap-2 text-xs font-semibold text-gray-300 uppercase tracking-wider">
          <Terminal size={14} className="text-green-400" />
          Developer Console
          <span className="ml-1 text-gray-600 font-normal normal-case">
            {logs.length} file{logs.length !== 1 ? 's' : ''} run
          </span>
        </span>
        {open ? <ChevronDown size={14} className="text-gray-500" /> : <ChevronRight size={14} className="text-gray-500" />}
      </button>

      {open && (
        <div
          ref={consoleRef}
          className="max-h-[36rem] overflow-y-auto divide-y divide-gray-800"
        >
          {logs.length === 0 ? (
            <p className="px-5 py-4 text-xs text-gray-600 italic">No test output yet…</p>
          ) : (
            logs.map((log, i) => {
              const isExpanded = expanded[i] ?? true
              const ok = log.exit_code === 0
              return (
                <div key={i} className="font-mono text-xs">
                  {/* File header row */}
                  <button
                    onClick={() => toggle(i)}
                    className="w-full flex items-center gap-2 px-4 py-2 hover:bg-gray-800/40 transition-colors text-left"
                  >
                    {ok
                      ? <CheckCircle2 size={12} className="flex-shrink-0 text-green-400" />
                      : <XCircle      size={12} className="flex-shrink-0 text-red-400"   />
                    }
                    <span className="flex-1 truncate text-gray-300">{log.source}</span>
                    <span className="flex items-center gap-3 text-gray-500 flex-shrink-0">
                      {log.passed  > 0 && <span className="text-green-400">{log.passed}✓</span>}
                      {log.failed  > 0 && <span className="text-red-400">{log.failed}✗</span>}
                      {log.skipped > 0 && <span className="text-yellow-500">{log.skipped}⊘</span>}
                    </span>
                    {isExpanded
                      ? <ChevronDown  size={11} className="text-gray-600 flex-shrink-0" />
                      : <ChevronRight size={11} className="text-gray-600 flex-shrink-0" />
                    }
                  </button>

                  {/* Output body */}
                  {isExpanded && (
                    <div className="bg-black/40 px-4 pb-3 pt-2 space-y-2">
                      {log.stdout && (
                        <TerminalOutput text={log.stdout} />
                      )}
                      {log.stderr && (
                        <>
                          {log.stdout && <div className="border-t border-gray-800 my-1" />}
                          <TerminalOutput text={log.stderr} className="opacity-90" />
                        </>
                      )}
                      {!log.stdout && !log.stderr && (
                        <span className="text-gray-700 italic text-xs font-mono">no output</span>
                      )}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ component
export default function RunDetail() {
  const { id: runId } = useParams<{ id: string }>()
  const { events, isConnected } = useRunWebSocket(runId ?? null)

  // Restore from cache immediately — avoids blank-state flash on back-navigation
  const cached = runId ? _runUICache.get(runId) : undefined

  const [nodes,        setNodes]        = useState<Record<string, NodeStatus>>(cached?.nodes        ?? {})
  const [llmStreams,   setLlmStreams]    = useState<Record<string, string>>   (cached?.llmStreams    ?? {})
  const [activeAgent,  setActiveAgent]  = useState<string | null>            (cached?.activeAgent   ?? null)
  const [savedTests,   setSavedTests]   = useState<string[]>                 (cached?.savedTests    ?? [])
  const [runResult,    setRunResult]    = useState<RunResult | null>         (cached?.runResult     ?? null)
  const [debugEntries, setDebugEntries] = useState<DebugEntry[]>             (cached?.debugEntries  ?? [])
  const [consoleLogs,  setConsoleLogs]  = useState<ConsoleLog[]>             (cached?.consoleLogs   ?? [])
  const [report,       setReport]       = useState                           (cached?.report        ?? '')
  const [finalStatus,  setFinalStatus]  = useState<string | null>            (cached?.finalStatus   ?? null)
  const [restarting,   setRestarting]   = useState(false)
  // If we restored from cache, skip REST seeding; already have live state
  const seededRef        = useRef(cached != null)
  // Track how many events have already been applied so we never double-process
  const processedCountRef = useRef(cached?.eventsProcessed ?? 0)
  const streamRef  = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()

  // ── REST baseline ────────────────────────────────────────────────────
  // Poll while running; stop once the run reaches a terminal state.
  const { data: restRun } = useQuery<TestRun>({
    queryKey: ['run', runId],
    queryFn: () => getRun(runId!),
    enabled: !!runId,
    refetchInterval: (data) => {
      if (data && FINAL_STATUSES.has(data.status)) return false
      if (isConnected) return false   // WS is live — REST polling not needed
      return 4_000
    },
  })

  useEffect(() => {
    if (!restRun || seededRef.current) return
    // Only seed from REST when there are no live WS events yet (avoids overwriting live data)
    if (events.length === 0) {
      seedFromRun(restRun, setNodes, setRunResult, setDebugEntries, setConsoleLogs, setReport, setSavedTests)
      seededRef.current = true
    }
  }, [restRun, events.length])

  // When REST shows completed and we have no WS events, force-seed regardless
  useEffect(() => {
    if (!restRun) return
    if (FINAL_STATUSES.has(restRun.status) && !finalStatus) {
      setFinalStatus(restRun.status)
      seedFromRun(restRun, setNodes, setRunResult, setDebugEntries, setConsoleLogs, setReport, setSavedTests)
      seededRef.current = true
    }
  }, [restRun, finalStatus])

  // ── WebSocket events ────────────────────────────────────────────────
  // Process only events we haven't seen yet (processedCountRef tracks the boundary).
  // This handles both normal incremental updates and the case where the component
  // remounts with a pre-populated events array restored from the module-level cache.
  useEffect(() => {
    const newEvents = events.slice(processedCountRef.current)
    if (newEvents.length === 0) return
    processedCountRef.current = events.length
    seededRef.current = true   // live events take priority over REST seeding

    for (const event of newEvents) {
      switch (event.type) {
        case 'progress':
          setNodes((prev) => ({
            ...prev,
            [event.node]: {
              status:  event.status === 'started' ? 'running'
                     : event.status === 'error'   ? 'error'
                     : 'done',
              message: event.message,
            },
          }))
          break
        case 'llm_token':
          setActiveAgent(event.agent)
          setLlmStreams((prev) => ({ ...prev, [event.agent]: (prev[event.agent] ?? '') + event.token }))
          requestAnimationFrame(() => {
            if (streamRef.current) streamRef.current.scrollTop = streamRef.current.scrollHeight
          })
          break
        case 'test_saved':
          setSavedTests((prev) => (prev.includes(event.path) ? prev : [...prev, event.path]))
          break
        case 'run_result':
          setRunResult(event)
          break
        case 'debug_result':
          setDebugEntries((prev) => [...prev, event])
          break
        case 'test_log':
          setConsoleLogs((prev) => [...prev, {
            source:    event.source,
            stdout:    event.stdout,
            stderr:    event.stderr,
            passed:    event.passed,
            failed:    event.failed,
            skipped:   event.skipped,
            exit_code: event.exit_code,
          }])
          break
        case 'complete':
          setFinalStatus(event.status)
          if (event.report) setReport(event.report)
          break
        case 'error':
          setFinalStatus('error')
          break
      }
    }
  }, [events])

  // ── Persist UI state to module-level cache ───────────────────────────
  // Runs after every render that changes derived state, so navigating back
  // always restores the latest snapshot instantly.
  useEffect(() => {
    if (!runId) return
    _runUICache.set(runId, {
      nodes, llmStreams, activeAgent, savedTests,
      runResult, debugEntries, consoleLogs, report, finalStatus,
      eventsProcessed: processedCountRef.current,
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, nodes, llmStreams, activeAgent, savedTests, runResult, debugEntries, consoleLogs, report, finalStatus])

  const currentStream = activeAgent ? (llmStreams[activeAgent] ?? '') : ''
  const displayStatus = finalStatus ?? restRun?.status ?? null
  const isFinished    = displayStatus ? FINAL_STATUSES.has(displayStatus) : false

  // A run is "stale" when the DB says running but no WS events arrived and no progress detected
  const isStale = restRun?.status === 'running'
    && !isConnected
    && events.length === 0
    && restRun?.error_message == null

  async function handleRestart() {
    if (!runId) return
    setRestarting(true)
    // Clear caches and reset local UI state for the fresh run
    _runUICache.delete(runId)
    processedCountRef.current = 0
    setNodes({}); setLlmStreams({}); setActiveAgent(null)
    setSavedTests([]); setRunResult(null); setDebugEntries([]); setConsoleLogs([])
    setReport(''); setFinalStatus(null); seededRef.current = false
    try {
      await restartRun(runId)
      await queryClient.invalidateQueries({ queryKey: ['run', runId] })
    } finally {
      setRestarting(false)
    }
  }

  // ------------------------------------------------------------------ render
  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/runs" className="text-gray-500 hover:text-gray-300 transition-colors">
            <ArrowLeft size={18} />
          </Link>
          <div>
            <h1 className="text-xl font-bold text-gray-100">
              Run Detail
              {restRun && (
                <span className="ml-2 text-sm font-normal text-gray-400">
                  PR #{restRun.pr_number} · {restRun.branch}
                </span>
              )}
            </h1>
            <p className="text-xs text-gray-600 font-mono mt-0.5">{runId}</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {displayStatus && (
            <span className={clsx('px-3 py-1 rounded-full text-xs font-semibold', STATUS_BADGE[displayStatus] ?? 'bg-gray-700 text-gray-300')}>
              {displayStatus}
            </span>
          )}
          <span className={clsx(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium',
            isConnected ? 'bg-green-500/10 text-green-400' : 'bg-gray-700/60 text-gray-500',
          )}>
            <span className={clsx('w-1.5 h-1.5 rounded-full', isConnected ? 'bg-green-400 animate-pulse' : 'bg-gray-500')} />
            {isConnected ? 'Live' : isFinished ? 'Completed' : 'Connecting…'}
          </span>
        </div>
      </div>

      {/* Stale / interrupted run banner */}
      {isStale && (
        <div className="flex items-center justify-between bg-yellow-500/10 border border-yellow-500/30 rounded-xl px-5 py-4">
          <div>
            <p className="text-sm font-semibold text-yellow-300">Pipeline not running</p>
            <p className="text-xs text-yellow-400/70 mt-0.5">
              This run has no active pipeline — the server may have restarted while it was in progress.
            </p>
          </div>
          <button
            onClick={handleRestart}
            disabled={restarting}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-yellow-500/20 hover:bg-yellow-500/30 text-yellow-300 text-sm font-medium transition-colors disabled:opacity-50"
          >
            <RefreshCw size={14} className={restarting ? 'animate-spin' : ''} />
            {restarting ? 'Restarting…' : 'Restart Run'}
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Pipeline stages */}
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-4">Pipeline</h2>
          <ol className="space-y-2.5">
            {PIPELINE_NODES.map((node, i) => {
              const ns = nodes[node.id]
              const s  = ns?.status ?? 'pending'
              return (
                <li key={node.id} className="flex items-start gap-3">
                  <span className={clsx(
                    'mt-0.5 flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold',
                    s === 'pending' && 'bg-gray-700 text-gray-600',
                    s === 'running' && 'bg-yellow-500/20 text-yellow-400 ring-1 ring-yellow-400 animate-pulse',
                    s === 'done'    && 'bg-green-500/20 text-green-400',
                    s === 'error'   && 'bg-red-500/20 text-red-400',
                  )}>
                    {s === 'done' ? '✓' : s === 'error' ? '✗' : i + 1}
                  </span>
                  <div>
                    <span className={clsx('text-sm', {
                      'text-gray-600':           s === 'pending',
                      'text-yellow-300 font-medium': s === 'running',
                      'text-gray-300':           s === 'done',
                      'text-red-400':            s === 'error',
                    })}>
                      {node.label}
                    </span>
                    {ns?.message && (
                      <p className="text-xs text-gray-500 mt-0.5">{ns.message}</p>
                    )}
                  </div>
                </li>
              )
            })}
          </ol>
        </div>

        {/* LLM streaming output */}
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700 flex flex-col min-h-[22rem]">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
              LLM Stream
              {activeAgent && (
                <span className="ml-2 text-indigo-400 font-normal normal-case">— {activeAgent}</span>
              )}
            </h2>
            {activeAgent && llmStreams[activeAgent] && (
              <span className="text-xs text-gray-600">{llmStreams[activeAgent].length} chars</span>
            )}
          </div>
          <div
            ref={streamRef}
            className="flex-1 overflow-y-auto rounded-lg bg-gray-950 p-3 font-mono text-xs text-green-300 whitespace-pre-wrap leading-relaxed"
          >
            {currentStream ? (
              <>
                {currentStream}
                {!isFinished && (
                  <span className="inline-block w-[2px] h-[1em] bg-green-400 ml-px align-middle animate-pulse" />
                )}
              </>
            ) : (
              <span className="text-gray-700 italic">
                {isConnected ? 'Waiting for LLM output…' : isFinished ? 'Pipeline complete — no stream to show.' : 'Connecting to live stream…'}
              </span>
            )}
          </div>
          {Object.keys(llmStreams).length > 1 && (
            <div className="flex gap-2 mt-3 flex-wrap">
              {Object.keys(llmStreams).map((agent) => (
                <button
                  key={agent}
                  onClick={() => setActiveAgent(agent)}
                  className={clsx(
                    'px-2 py-0.5 rounded text-xs font-medium transition-colors',
                    activeAgent === agent ? 'bg-indigo-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600',
                  )}
                >
                  {agent}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Generated tests */}
      {savedTests.length > 0 && (
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Generated Tests ({savedTests.length})
          </h2>
          <ul className="space-y-1">
            {savedTests.map((path) => (
              <li key={path} className="font-mono text-xs text-gray-300 bg-gray-900 rounded px-2 py-1.5 border border-gray-700/50">
                {path}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Test results */}
      {runResult && (
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-4">Test Results</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: 'Passed',   value: runResult.passed,                   color: 'text-green-400'  },
              { label: 'Failed',   value: runResult.failed,                   color: 'text-red-400'    },
              { label: 'Skipped',  value: runResult.skipped,                  color: 'text-yellow-400' },
              { label: 'Duration', value: `${runResult.duration.toFixed(1)}s`, color: 'text-gray-300'   },
            ].map(({ label, value, color }) => (
              <div key={label} className="bg-gray-900 rounded-lg p-4 text-center border border-gray-700/50">
                <p className={clsx('text-2xl font-bold tabular-nums', color)}>{value}</p>
                <p className="text-xs text-gray-500 mt-1">{label}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Debug entries */}
      {debugEntries.length > 0 && (
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Debug Analysis ({debugEntries.length})
          </h2>
          <div className="space-y-3">
            {debugEntries.map((d, i) => (
              <div key={i} className="bg-gray-900 rounded-lg p-4 border border-gray-700/50 space-y-2">
                <p className="font-mono text-xs text-yellow-400 break-all">{d.test_name}</p>
                <div>
                  <p className="text-xs text-gray-500 mb-0.5">Root cause</p>
                  <Markdown className="text-xs">{d.root_cause}</Markdown>
                </div>
                <div>
                  <p className="text-xs text-gray-500 mb-0.5">Fix suggestion</p>
                  <Markdown className="text-xs">{d.fix_suggestion}</Markdown>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex-1 bg-gray-700 rounded-full h-1">
                    <div
                      className={clsx('h-1 rounded-full transition-all', {
                        'bg-green-400':  d.confidence >= 70,
                        'bg-yellow-400': d.confidence >= 40 && d.confidence < 70,
                        'bg-red-400':    d.confidence < 40,
                      })}
                      style={{ width: `${d.confidence}%` }}
                    />
                  </div>
                  <span className="text-xs text-gray-500 tabular-nums">{d.confidence}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Developer Console */}
      {(consoleLogs.length > 0 || (isFinished && restRun)) && (
        <DevConsole logs={consoleLogs} />
      )}

      {/* Error from REST */}
      {restRun?.error_message && (
        <div className="bg-gray-800 rounded-xl p-5 border border-red-500/20">
          <h2 className="text-xs font-semibold text-red-400 uppercase tracking-wider mb-2">Pipeline Error</h2>
          <pre className="text-xs text-red-300 bg-gray-900 rounded-lg p-3 whitespace-pre-wrap overflow-x-auto">
            {restRun.error_message}
          </pre>
        </div>
      )}

      {/* Final report */}
      {report && (
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Report</h2>
          <div className="bg-gray-900 rounded-lg p-4 border border-gray-700/50">
            <Markdown>{report}</Markdown>
          </div>
        </div>
      )}

      {/* Empty state — nothing from WS or REST yet */}
      {!restRun && !isConnected && events.length === 0 && (
        <div className="text-center py-16 text-gray-600">
          <p className="text-sm">Connecting to pipeline…</p>
        </div>
      )}
    </div>
  )
}
