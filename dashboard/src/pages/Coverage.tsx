import { useCoverage } from '../hooks/useTestData'
import CoverageTrendChart from '../components/CoverageTrendChart'

export default function Coverage() {
  const { data: coverage = [] } = useCoverage()
  const services = [...new Set(coverage.map((c) => c.service))]

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-gray-100">Coverage</h1>
        <p className="text-gray-400 text-sm mt-1">Coverage trends across all services</p>
      </div>
      <CoverageTrendChart data={coverage} />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {services.map((svc) => {
          const latest = [...coverage].filter((c) => c.service === svc).pop()
          if (!latest) return null
          return (
            <div key={svc} className="bg-gray-800 rounded-xl p-5 border border-gray-700">
              <h3 className="text-sm font-semibold text-gray-300 mb-3 capitalize">{svc}</h3>
              <div className="space-y-2">
                {(['lines', 'branches', 'functions', 'statements'] as const).map((metric) => (
                  <div key={metric} className="flex justify-between items-center">
                    <span className="text-xs text-gray-500 capitalize">{metric}</span>
                    <div className="flex items-center gap-2">
                      <div className="w-24 h-1.5 bg-gray-700 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-indigo-500 rounded-full"
                          style={{ width: `${latest[metric]}%` }}
                        />
                      </div>
                      <span className="text-xs font-mono text-gray-300 w-10 text-right">{latest[metric].toFixed(1)}%</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
