import { Activity, CheckCircle, TrendingUp, AlertTriangle } from 'lucide-react'
import { useRuns, useCoverage, useFlakiness, useAgentStatus } from '../hooks/useTestData'
import AgentStatusCard from '../components/AgentStatusCard'
import TestRunTable from '../components/TestRunTable'
import CoverageTrendChart from '../components/CoverageTrendChart'
import FlakinessHeatmap from '../components/FlakinessHeatmap'
import PRSummaryCard from '../components/PRSummaryCard'
import BranchSelector from '../components/BranchSelector'

interface StatCardProps {
  label: string
  value: string | number
  sub?: string
  icon: React.ReactNode
  color: string
}

function StatCard({ label, value, sub, icon, color }: StatCardProps) {
  return (
    <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-gray-400 uppercase tracking-wider font-semibold">{label}</span>
        <span className={color}>{icon}</span>
      </div>
      <p className="text-3xl font-bold text-gray-100">{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
    </div>
  )
}

export default function Dashboard() {
  const { data: runs = [] } = useRuns()
  const { data: coverage = [] } = useCoverage()
  const { data: flakiness = [] } = useFlakiness()
  const { data: agents = [] } = useAgentStatus()

  const totalRuns = runs.length
  const passed = runs.filter((r) => r.status === 'passed').length
  const passRate = totalRuns > 0 ? Math.round((passed / totalRuns) * 100) : 0
  const latestCoverage = coverage.length > 0 ? coverage[coverage.length - 1].lines.toFixed(1) : '—'
  const flakyCount = flakiness.filter((f) => f.score >= 70).length

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-100">Overview</h1>
        <p className="text-gray-400 text-sm mt-1">Live test intelligence dashboard</p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <StatCard label="Total Runs" value={totalRuns} sub="all time" icon={<Activity size={18} />} color="text-indigo-400" />
        <StatCard label="Pass Rate" value={`${passRate}%`} sub={`${passed}/${totalRuns} passed`} icon={<CheckCircle size={18} />} color="text-green-400" />
        <StatCard label="Avg Coverage" value={`${latestCoverage}%`} sub="latest snapshot" icon={<TrendingUp size={18} />} color="text-sky-400" />
        <StatCard label="Flaky Tests" value={flakyCount} sub="score ≥ 70" icon={<AlertTriangle size={18} />} color="text-yellow-400" />
      </div>

      {/* Main grid */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2 space-y-4">
          <CoverageTrendChart data={coverage} />
          <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">Recent Runs</h2>
            <TestRunTable runs={runs} limit={5} />
          </div>
        </div>
        <div className="space-y-4">
          <AgentStatusCard agents={agents} />
          {runs[0] && <PRSummaryCard run={runs[0]} />}
          <BranchSelector />
          <FlakinessHeatmap data={flakiness} />
        </div>
      </div>
    </div>
  )
}
