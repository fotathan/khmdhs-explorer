import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { Users, Search, TrendingUp, Globe } from 'lucide-react'
import { supabase } from '../lib/supabase'
import { formatCurrency } from '../lib/format'
import LoadingSpinner from '../components/LoadingSpinner'
import type { Contractor } from '../lib/types'

export default function Contractors() {
  const [contractors, setContractors] = useState<Contractor[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState<'total_value' | 'act_count' | 'name'>('total_value')

  useEffect(() => {
    loadContractors()
  }, [sortBy])

  async function loadContractors() {
    setLoading(true)
    const { data } = await supabase
      .from('contractors')
      .select('*')
      .order(sortBy, { ascending: sortBy === 'name' })
      .limit(50)
    setContractors(data ?? [])
    setLoading(false)
  }

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    let q = supabase
      .from('contractors')
      .select('*')
      .order(sortBy, { ascending: sortBy === 'name' })
      .limit(50)

    if (search.trim()) {
      q = q.ilike('name', `%${search.trim()}%`)
    }
    const { data } = await q
    setContractors(data ?? [])
    setLoading(false)
  }

  return (
    <div className="space-y-6">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-bold text-slate-900">Contractors</h1>
        <p className="text-sm text-slate-500 mt-1">
          Directory of economic operators and service providers
        </p>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        className="bg-white rounded-xl border border-slate-200 p-4"
      >
        <form onSubmit={handleSearch} className="flex gap-3">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search contractors by name..."
              className="w-full pl-10 pr-4 py-2.5 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500 transition-all"
            />
          </div>
          <button
            type="submit"
            className="px-5 py-2.5 bg-teal-700 text-white text-sm font-medium rounded-lg hover:bg-teal-800 transition-colors"
          >
            Search
          </button>
        </form>
        <div className="flex gap-2 mt-3">
          {[
            { key: 'total_value' as const, label: 'By Value' },
            { key: 'act_count' as const, label: 'By Activity' },
            { key: 'name' as const, label: 'Alphabetical' },
          ].map(opt => (
            <button
              key={opt.key}
              onClick={() => setSortBy(opt.key)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                sortBy === opt.key
                  ? 'bg-teal-50 text-teal-700 border border-teal-200'
                  : 'bg-slate-50 text-slate-600 border border-transparent hover:bg-slate-100'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </motion.div>

      {loading ? (
        <LoadingSpinner text="Loading contractors..." />
      ) : (
        <div className="space-y-3">
          {contractors.map((contractor, i) => (
            <motion.div
              key={contractor.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: Math.min(i * 0.03, 0.3) }}
              className="bg-white rounded-xl border border-slate-200 p-4 hover:shadow-md hover:border-slate-300 transition-all duration-200"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <Users className="w-4 h-4 text-emerald-600 flex-shrink-0" />
                    <h3 className="text-sm font-medium text-slate-800 truncate">{contractor.name}</h3>
                  </div>
                  <div className="flex items-center gap-4 mt-2 text-xs text-slate-500">
                    {contractor.vat_number && <span>VAT: {contractor.vat_number}</span>}
                    {contractor.country && (
                      <span className="flex items-center gap-1">
                        <Globe className="w-3 h-3" /> {contractor.country}
                      </span>
                    )}
                  </div>
                </div>
                <div className="text-right flex-shrink-0 space-y-1">
                  <div className="flex items-center gap-1.5 justify-end">
                    <TrendingUp className="w-3 h-3 text-emerald-500" />
                    <span className="text-sm font-semibold text-slate-900">
                      {formatCurrency(contractor.total_value)}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400">{contractor.act_count} acts</p>
                </div>
              </div>
            </motion.div>
          ))}

          {contractors.length === 0 && (
            <div className="text-center py-12">
              <Users className="w-10 h-10 text-slate-300 mx-auto mb-3" />
              <p className="text-sm text-slate-500">No contractors found</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
