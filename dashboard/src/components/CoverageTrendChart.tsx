import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import type { CoverageReport } from '../types'

interface Props {
  data: CoverageReport[]
}

const SERVICE_COLORS: Record<string, string> = {
  frontend: '#818cf8',
  api: '#34d399',
  nestjs: '#fb923c',
  fastapi: '#f472b6',
}

function fallbackColor(index: number) {
  const palette = ['#818cf8', '#34d399', '#fb923c', '#f472b6', '#facc15']
  return palette[index % palette.length]
}

export default function CoverageTrendChart({ data }: Props) {
  const services = [...new Set(data.map((d) => d.service))]

  // Pivot data by timestamp
  const byTimestamp = data.reduce<Record<string, Record<string, number>>>((acc, row) => {
    const ts = new Date(row.timestamp).toLocaleDateString()
    if (!acc[ts]) acc[ts] = { ts: ts as unknown as number }
    acc[ts][row.service] = row.lines
    return acc
  }, {})
  const chartData = Object.values(byTimestamp)

  return (
    <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">Coverage Trend (Lines %)</h2>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={chartData} margin={{ top: 5, right: 10, left: -10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="ts" tick={{ fill: '#6b7280', fontSize: 11 }} />
          <YAxis domain={[0, 100]} tick={{ fill: '#6b7280', fontSize: 11 }} unit="%" />
          <Tooltip
            contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: 8 }}
            labelStyle={{ color: '#e5e7eb' }}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: '#9ca3af' }} />
          {services.map((svc, i) => (
            <Line
              key={svc}
              type="monotone"
              dataKey={svc}
              stroke={SERVICE_COLORS[svc] ?? fallbackColor(i)}
              strokeWidth={2}
              dot={{ r: 3 }}
              activeDot={{ r: 5 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
