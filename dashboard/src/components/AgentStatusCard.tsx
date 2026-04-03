import clsx from 'clsx'
import type { AgentStatus } from '../types'

interface Props {
  agents: AgentStatus[]
}

const statusColor = {
  idle: 'bg-gray-500',
  running: 'bg-green-400',
  error: 'bg-red-500',
}

const statusLabel = {
  idle: 'Idle',
  running: 'Running',
  error: 'Error',
}

export default function AgentStatusCard({ agents }: Props) {
  return (
    <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">Agent Status</h2>
      <div className="space-y-3">
        {agents.map((agent) => (
          <div key={agent.name} className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span
                className={clsx(
                  'w-2.5 h-2.5 rounded-full flex-shrink-0',
                  statusColor[agent.status],
                  agent.status === 'running' && 'animate-pulse'
                )}
              />
              <div>
                <p className="text-sm font-medium text-gray-200">{agent.name}</p>
                {agent.current_task && (
                  <p className="text-xs text-gray-500 truncate max-w-[180px]">{agent.current_task}</p>
                )}
              </div>
            </div>
            <span
              className={clsx(
                'text-xs px-2 py-0.5 rounded-full font-medium',
                agent.status === 'running' && 'bg-green-400/10 text-green-400',
                agent.status === 'idle' && 'bg-gray-700 text-gray-400',
                agent.status === 'error' && 'bg-red-500/10 text-red-400'
              )}
            >
              {statusLabel[agent.status]}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
