import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Search from './pages/Search'
import ActDetail from './pages/ActDetail'
import Authorities from './pages/Authorities'
import Contractors from './pages/Contractors'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/search" element={<Search />} />
        <Route path="/act/:adam" element={<ActDetail />} />
        <Route path="/authorities" element={<Authorities />} />
        <Route path="/contractors" element={<Contractors />} />
      </Route>
    </Routes>
  )
}
