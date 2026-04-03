import { useFlakiness } from '../hooks/useTestData'
import FlakinessHeatmap from '../components/FlakinessHeatmap'

export default function Flakiness() {
  const { data: flakiness = [] } = useFlakiness()
  const critical = flakiness.filter((f) => f.score >= 70).length
  const warning = flakiness.filter((f) => f.score >= 40 && f.score < 70).length
  const stable = flakiness.filter((f) => f.score < 40).length

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-gray-100">Flakiness</h1>
        <p className="text-gray-400 text-sm mt-1">Tests ranked by instability score (0–100)</p>
      </div>
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-center">
          <p className="text-3xl font-bold text-red-400">{critical}</p>
          <p className="text-xs text-red-400/70 mt-1">Critical (≥70)</p>
        </div>
        <div className="bg-yellow-400/10 border border-yellow-400/20 rounded-xl p-4 text-center">
          <p className="text-3xl font-bold text-yellow-400">{warning}</p>
          <p className="text-xs text-yellow-400/70 mt-1">Warning (40–69)</p>
        </div>
        <div className="bg-green-500/10 border border-green-500/20 rounded-xl p-4 text-center">
          <p className="text-3xl font-bold text-green-400">{stable}</p>
          <p className="text-xs text-green-400/70 mt-1">Stable (&lt;40)</p>
        </div>
      </div>
      <FlakinessHeatmap data={flakiness} />
    </div>
  )
}
