import { useQuery } from '@tanstack/react-query'
import { getRuns, getCoverage, getFlakiness, getAgentStatus, getLocalBranches, getCurrentBranch } from '../api/client'

export function useRuns() {
  return useQuery({ queryKey: ['runs'], queryFn: getRuns, refetchInterval: 10_000 })
}

export function useCoverage() {
  return useQuery({ queryKey: ['coverage'], queryFn: getCoverage, refetchInterval: 30_000 })
}

export function useFlakiness() {
  return useQuery({ queryKey: ['flakiness'], queryFn: getFlakiness, refetchInterval: 30_000 })
}

export function useAgentStatus() {
  return useQuery({ queryKey: ['agents'], queryFn: getAgentStatus, refetchInterval: 5_000 })
}

export function useLocalBranches(repoPath = '.') {
  return useQuery({
    queryKey: ['branches', 'local', repoPath],
    queryFn: () => getLocalBranches(repoPath),
    staleTime: 30_000,
  })
}

export function useCurrentBranch(repoPath = '.') {
  return useQuery({
    queryKey: ['branches', 'current', repoPath],
    queryFn: () => getCurrentBranch(repoPath),
    staleTime: 10_000,
  })
}
