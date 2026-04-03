import { useState } from 'react'
import { GitBranch, RefreshCw, Search } from 'lucide-react'
import { useLocalBranches, useCurrentBranch } from '../hooks/useTestData'
import { useQuery } from '@tanstack/react-query'
import { getRemoteBranches } from '../api/client'
import clsx from 'clsx'

export default function Branches() {
  const [repoInput, setRepoInput] = useState('')
  const [submittedRepo, setSubmittedRepo] = useState('')
  const [search, setSearch] = useState('')

  const { data: localBranches = [], isFetching: localLoading, refetch: refetchLocal } = useLocalBranches()
  const { data: current } = useCurrentBranch()

  const { data: remoteBranches = [], isFetching: remoteLoading, error: remoteError } = useQuery({
    queryKey: ['branches', 'remote', submittedRepo],
    queryFn: () => getRemoteBranches(submittedRepo),
    enabled: submittedRepo.length > 0,
    retry: false,
  })

  const filterBranches = (list: string[]) =>
    search ? list.filter((b) => b.toLowerCase().includes(search.toLowerCase())) : list

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-100">Branches</h1>
        <p className="text-gray-400 text-sm mt-1">Local and remote branch explorer</p>
      </div>

      {/* Search */}
      <div className="relative">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
        <input
          type="text"
          placeholder="Filter branches..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-4 py-2 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Local branches */}
        <div className="bg-gray-800 rounded-xl border border-gray-700">
          <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
            <div className="flex items-center gap-2">
              <GitBranch size={16} className="text-indigo-400" />
              <h2 className="text-sm font-semibold text-gray-300">Local Branches</h2>
              <span className="text-xs bg-gray-700 text-gray-400 px-2 py-0.5 rounded-full">
                {filterBranches(localBranches).length}
              </span>
            </div>
            <button
              onClick={() => refetchLocal()}
              className="text-gray-500 hover:text-gray-300 transition-colors"
            >
              <RefreshCw size={14} className={localLoading ? 'animate-spin' : ''} />
            </button>
          </div>
          <ul className="divide-y divide-gray-700/50 max-h-96 overflow-y-auto">
            {filterBranches(localBranches).map((branch) => (
              <li key={branch} className="flex items-center gap-3 px-5 py-3 hover:bg-gray-700/40 transition-colors">
                <GitBranch size={13} className="text-gray-500 flex-shrink-0" />
                <span className="text-sm font-mono text-gray-200 truncate flex-1">{branch}</span>
                {branch === current?.branch && (
                  <span className="text-xs bg-indigo-600/20 text-indigo-400 px-2 py-0.5 rounded-full flex-shrink-0">
                    active
                  </span>
                )}
              </li>
            ))}
            {filterBranches(localBranches).length === 0 && (
              <li className="px-5 py-6 text-sm text-gray-500 text-center">No branches found</li>
            )}
          </ul>
        </div>

        {/* Remote branches */}
        <div className="bg-gray-800 rounded-xl border border-gray-700">
          <div className="flex items-center gap-2 px-5 py-4 border-b border-gray-700">
            <GitBranch size={16} className="text-green-400" />
            <h2 className="text-sm font-semibold text-gray-300">Remote Branches</h2>
            {remoteBranches.length > 0 && (
              <span className="text-xs bg-gray-700 text-gray-400 px-2 py-0.5 rounded-full">
                {filterBranches(remoteBranches).length}
              </span>
            )}
          </div>

          {/* Repo input */}
          <div className="px-5 py-3 border-b border-gray-700">
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="owner/repo-name"
                value={repoInput}
                onChange={(e) => setRepoInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && setSubmittedRepo(repoInput.trim())}
                className="flex-1 bg-gray-900 border border-gray-600 rounded-lg px-3 py-1.5 text-sm font-mono text-gray-200 placeholder-gray-600 focus:outline-none focus:border-indigo-500"
              />
              <button
                onClick={() => setSubmittedRepo(repoInput.trim())}
                disabled={!repoInput.trim() || remoteLoading}
                className={clsx(
                  'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
                  repoInput.trim()
                    ? 'bg-indigo-600 hover:bg-indigo-500 text-white'
                    : 'bg-gray-700 text-gray-500 cursor-not-allowed'
                )}
              >
                {remoteLoading ? <RefreshCw size={14} className="animate-spin" /> : 'Load'}
              </button>
            </div>
            {remoteError && (
              <p className="text-xs text-red-400 mt-1">
                Failed to load — check repo name and GITHUB_TOKEN
              </p>
            )}
          </div>

          <ul className="divide-y divide-gray-700/50 max-h-80 overflow-y-auto">
            {filterBranches(remoteBranches).map((branch) => (
              <li key={branch} className="flex items-center gap-3 px-5 py-3 hover:bg-gray-700/40 transition-colors">
                <GitBranch size={13} className="text-gray-500 flex-shrink-0" />
                <span className="text-sm font-mono text-gray-200 truncate">{branch}</span>
              </li>
            ))}
            {submittedRepo && !remoteLoading && filterBranches(remoteBranches).length === 0 && !remoteError && (
              <li className="px-5 py-6 text-sm text-gray-500 text-center">No branches found</li>
            )}
            {!submittedRepo && (
              <li className="px-5 py-6 text-sm text-gray-500 text-center">Enter a repo above to load branches</li>
            )}
          </ul>
        </div>
      </div>
    </div>
  )
}
