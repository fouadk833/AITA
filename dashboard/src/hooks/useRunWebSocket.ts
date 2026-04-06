import { useEffect, useRef, useState } from 'react'

export type WSEvent =
  | { type: 'connected'; run_id: string }
  | { type: 'progress'; node: string; status: 'started' | 'done' | 'error'; message?: string }
  | { type: 'log'; level: 'info' | 'warning' | 'error'; logger: string; message: string }
  | { type: 'test_saved'; path: string; layer: 'unit' | 'integration' | 'e2e' }
  | { type: 'run_result'; passed: number; failed: number; skipped: number; duration: number }
  | { type: 'debug_result'; test_name: string; root_cause: string; fix_suggestion: string; confidence: number }
  | { type: 'test_log'; source: string; stdout: string; stderr: string; passed: number; failed: number; skipped: number; exit_code: number }
  | { type: 'complete'; status: string; report: string }
  | { type: 'error'; message: string }

// Module-level event cache — survives navigation (cleared on page refresh only)
const _wsEventCache = new Map<string, WSEvent[]>()

export function useRunWebSocket(runId: string | null) {
  const cachedEvents = runId ? (_wsEventCache.get(runId) ?? []) : []
  const [events, setEvents] = useState<WSEvent[]>(cachedEvents)
  const [isConnected, setIsConnected] = useState(false)
  // How many events were in the cache when this WS session started.
  // We skip that many messages from the server's replay buffer to avoid duplicates.
  const skipCountRef = useRef(0)
  const receivedRef  = useRef(0)

  useEffect(() => {
    if (!runId) return

    skipCountRef.current = _wsEventCache.get(runId)?.length ?? 0
    receivedRef.current  = 0

    const base  = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'
    const wsUrl = base.replace(/^http/, 'ws') + `/ws/runs/${runId}`
    const ws    = new WebSocket(wsUrl)

    ws.onopen  = () => setIsConnected(true)
    ws.onclose = () => setIsConnected(false)
    ws.onerror = () => setIsConnected(false)
    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data as string) as WSEvent
        receivedRef.current++
        // Skip events that are part of the server-side replay buffer and are
        // already reflected in our client-side cache.
        if (receivedRef.current <= skipCountRef.current) return
        setEvents((prev) => {
          const next = [...prev, event]
          _wsEventCache.set(runId, next)
          return next
        })
      } catch {
        // ignore malformed frames
      }
    }

    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send('ping')
    }, 15_000)

    return () => {
      clearInterval(ping)
      ws.close()
    }
  }, [runId])

  return { events, isConnected }
}
