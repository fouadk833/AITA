import { GitBranch } from 'lucide-react'
import { useLocalBranches, useCurrentBranch } from '../hooks/useTestData'

interface Props {
  onSelect?: (branch: string) => void
}

export default function BranchSelector({ onSelect }: Props) {
  const { data: branches = [] } = useLocalBranches()
  const { data: current } = useCurrentBranch()

  return (
    <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
      <div className="flex items-center gap-2 mb-4">
        <GitBranch size={16} className="text-indigo-400" />
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Branches</h2>
        {current && (
          <span className="ml-auto text-xs bg-indigo-600/20 text-indigo-400 px-2 py-0.5 rounded-full font-mono">
            {current.branch}
          </span>
        )}
      </div>
      <ul className="space-y-1">
        {branches.map((branch) => (
          <li key={branch}>
            <button
              onClick={() => onSelect?.(branch)}
              className="w-full text-left flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-mono text-gray-300 hover:bg-gray-700 transition-colors"
            >
              <GitBranch size={13} className="text-gray-500 flex-shrink-0" />
              <span className="truncate">{branch}</span>
              {branch === current?.branch && (
                <span className="ml-auto text-xs text-indigo-400">active</span>
              )}
            </button>
          </li>
        ))}
        {branches.length === 0 && (
          <p className="text-xs text-gray-500 text-center py-2">No branches found</p>
        )}
      </ul>
    </div>
  )
}
