import { NavLink, Outlet } from 'react-router-dom'
import { LayoutDashboard, ListChecks, BarChart2, AlertTriangle, GitBranch, Zap } from 'lucide-react'
import clsx from 'clsx'

const nav = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/runs', label: 'Test Runs', icon: ListChecks },
  // { to: '/coverage', label: 'Coverage', icon: BarChart2 },
  // { to: '/flakiness', label: 'Flakiness', icon: AlertTriangle },
  // { to: '/branches', label: 'Branches', icon: GitBranch },
]

export default function Layout() {
  return (
    <div className="flex h-screen bg-gray-900 text-gray-100">
      {/* Sidebar */}
      <aside className="w-60 flex-shrink-0 bg-gray-800 flex flex-col border-r border-gray-700">
        <div className="flex items-center gap-2 px-5 py-5 border-b border-gray-700">
          <Zap className="text-indigo-400" size={22} />
          <span className="font-bold text-lg tracking-tight">AITA</span>
          <span className="text-xs text-gray-400 mt-0.5">platform</span>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-indigo-600 text-white'
                    : 'text-gray-400 hover:bg-gray-700 hover:text-gray-100'
                )
              }
            >
              <Icon size={17} />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
