import clsx from 'clsx'
import { GitPullRequest } from 'lucide-react'
import type { TestRun } from '../types'

interface Props {
  run: TestRun
}

export default function PRSummaryCard({ run }: Props) {
  const total = run.passed + run.failed + run.skipped
  const passRate = total > 0 ? Math.round((run.passed / total) * 100) : 0

  return (
    <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
      <div className="flex items-center gap-2 mb-4">
        <GitPullRequest size={16} className="text-indigo-400" />
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Latest PR</h2>
      </div>
      <div className="space-y-2">
        <div className="flex justify-between text-sm">
          <span className="text-gray-400">PR</span>
          <span className="font-mono text-indigo-400">#{run.pr_number}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-400">Branch</span>
          <span className="font-mono text-gray-300 text-xs">{run.branch}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-400">Commit</span>
          <span className="font-mono text-gray-500 text-xs">{run.commit_sha.slice(0, 7)}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-400">Pass Rate</span>
          <span className={clsx('font-bold', passRate >= 80 ? 'text-green-400' : passRate >= 50 ? 'text-yellow-400' : 'text-red-400')}>
            {passRate}%
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-400">Tests</span>
          <span className="text-gray-300">
            <span className="text-green-400">{run.passed}</span>
            {' / '}
            <span className="text-red-400">{run.failed}</span>
            {' / '}
            <span className="text-gray-500">{run.skipped}</span>
            <span className="text-gray-600 text-xs ml-1">(p/f/s)</span>
          </span>
        </div>
      </div>
    </div>
  )
}
