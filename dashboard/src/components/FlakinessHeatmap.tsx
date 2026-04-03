import clsx from 'clsx'
import type { FlakinessScore } from '../types'

interface Props {
  data: FlakinessScore[]
}

function rowColor(score: number) {
  if (score >= 70) return 'border-l-4 border-red-500 bg-red-500/5'
  if (score >= 40) return 'border-l-4 border-yellow-400 bg-yellow-400/5'
  return 'border-l-4 border-green-500 bg-green-500/5'
}

function badgeColor(score: number) {
  if (score >= 70) return 'bg-red-500/20 text-red-400'
  if (score >= 40) return 'bg-yellow-400/20 text-yellow-400'
  return 'bg-green-500/20 text-green-400'
}

export default function FlakinessHeatmap({ data }: Props) {
  const sorted = [...data].sort((a, b) => b.score - a.score)

  return (
    <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">Flakiness Heatmap</h2>
      <div className="space-y-2">
        {sorted.map((item) => (
          <div key={item.test_name} className={clsx('px-4 py-3 rounded-lg', rowColor(item.score))}>
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-200 truncate">{item.test_name}</p>
                <p className="text-xs text-gray-500 truncate">{item.file_path}</p>
              </div>
              <div className="flex items-center gap-3 flex-shrink-0">
                <span className="text-xs text-gray-400">{item.failure_count}/{item.run_count} fails</span>
                <span className={clsx('text-xs font-bold px-2 py-0.5 rounded-full', badgeColor(item.score))}>
                  {item.score}
                </span>
              </div>
            </div>
          </div>
        ))}
        {sorted.length === 0 && (
          <p className="text-sm text-gray-500 text-center py-4">No flaky tests detected</p>
        )}
      </div>
    </div>
  )
}
