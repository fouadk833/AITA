import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import TestRuns from './pages/TestRuns'
import RunDetail from './pages/RunDetail'
import Coverage from './pages/Coverage'
import Flakiness from './pages/Flakiness'
import Branches from './pages/Branches'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5_000 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="runs" element={<TestRuns />} />
            <Route path="runs/:id" element={<RunDetail />} />
            <Route path="coverage" element={<Coverage />} />
            <Route path="flakiness" element={<Flakiness />} />
            <Route path="branches" element={<Branches />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
