import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Search as SearchIcon, ListFilter as Filter, X, Calendar, MapPin, ChevronDown } from 'lucide-react'
import { supabase } from '../lib/supabase'
import {
  formatCurrency, formatDate, getActTypeLabel, getActTypeColor,
  getStatusColor, getStatusLabel, getContractTypeLabel,
} from '../lib/format'
import LoadingSpinner from '../components/LoadingSpinner'
import type { ProcurementAct } from '../lib/types'

const ACT_TYPES = ['request', 'notice', 'auction', 'contract', 'payment'] as const
const CONTRACT_TYPES = ['supplies', 'services', 'works'] as const

export default function Search() {
  const [acts, setActs] = useState<ProcurementAct[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [contractTypeFilter, setContractTypeFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [showFilters, setShowFilters] = useState(false)

  useEffect(() => {
    loadActs()
  }, [typeFilter, contractTypeFilter, statusFilter])

  async function loadActs() {
    setLoading(true)
    let q = supabase
      .from('procurement_acts')
      .select('*')
      .order('submission_date', { ascending: false })
      .limit(50)

    if (typeFilter) q = q.eq('type', typeFilter)
    if (contractTypeFilter) q = q.eq('contract_type', contractTypeFilter)
    if (statusFilter) q = q.eq('status', statusFilter)

    const { data } = await q
    setActs(data ?? [])
    setLoading(false)
  }

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    let q = supabase
      .from('procurement_acts')
      .select('*')
      .order('submission_date', { ascending: false })
      .limit(50)

    if (query.trim()) {
      q = q.ilike('title', `%${query.trim()}%`)
    }
    if (typeFilter) q = q.eq('type', typeFilter)
    if (contractTypeFilter) q = q.eq('contract_type', contractTypeFilter)
    if (statusFilter) q = q.eq('status', statusFilter)

    const { data } = await q
    setActs(data ?? [])
    setLoading(false)
  }

  function clearFilters() {
    setTypeFilter('')
    setContractTypeFilter('')
    setStatusFilter('')
    setQuery('')
  }

  const hasActiveFilters = typeFilter || contractTypeFilter || statusFilter || query

  return (
    <div className="space-y-6">
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <h1 className="text-2xl font-bold text-slate-900">Search Procurement Acts</h1>
        <p className="text-sm text-slate-500 mt-1">
          Browse and filter public procurement data
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
            <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by title, ADAM code..."
              className="w-full pl-10 pr-4 py-2.5 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500 transition-all"
            />
          </div>
          <button
            type="submit"
            className="px-5 py-2.5 bg-teal-700 text-white text-sm font-medium rounded-lg hover:bg-teal-800 transition-colors"
          >
            Search
          </button>
          <button
            type="button"
            onClick={() => setShowFilters(!showFilters)}
            className={`px-3 py-2.5 border rounded-lg transition-all ${
              showFilters ? 'border-teal-500 bg-teal-50 text-teal-700' : 'border-slate-200 text-slate-600 hover:bg-slate-50'
            }`}
          >
            <Filter className="w-4 h-4" />
          </button>
        </form>

        <AnimatePresence>
          {showFilters && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              <div className="pt-4 mt-4 border-t border-slate-100 grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div>
                  <label className="text-xs font-medium text-slate-500 mb-1.5 block">Act Type</label>
                  <div className="relative">
                    <select
                      value={typeFilter}
                      onChange={(e) => setTypeFilter(e.target.value)}
                      className="w-full appearance-none pl-3 pr-8 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500"
                    >
                      <option value="">All types</option>
                      {ACT_TYPES.map(t => (
                        <option key={t} value={t}>{getActTypeLabel(t)}</option>
                      ))}
                    </select>
                    <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
                  </div>
                </div>

                <div>
                  <label className="text-xs font-medium text-slate-500 mb-1.5 block">Contract Type</label>
                  <div className="relative">
                    <select
                      value={contractTypeFilter}
                      onChange={(e) => setContractTypeFilter(e.target.value)}
                      className="w-full appearance-none pl-3 pr-8 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500"
                    >
                      <option value="">All categories</option>
                      {CONTRACT_TYPES.map(t => (
                        <option key={t} value={t}>{getContractTypeLabel(t)}</option>
                      ))}
                    </select>
                    <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
                  </div>
                </div>

                <div>
                  <label className="text-xs font-medium text-slate-500 mb-1.5 block">Status</label>
                  <div className="relative">
                    <select
                      value={statusFilter}
                      onChange={(e) => setStatusFilter(e.target.value)}
                      className="w-full appearance-none pl-3 pr-8 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500"
                    >
                      <option value="">All statuses</option>
                      <option value="active">Active</option>
                      <option value="cancelled">Cancelled</option>
                      <option value="modified">Modified</option>
                    </select>
                    <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
                  </div>
                </div>
              </div>

              {hasActiveFilters && (
                <div className="mt-3 flex items-center gap-2">
                  <button
                    onClick={clearFilters}
                    className="text-xs text-slate-500 hover:text-slate-700 flex items-center gap-1 transition-colors"
                  >
                    <X className="w-3 h-3" /> Clear all filters
                  </button>
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>

      {loading ? (
        <LoadingSpinner text="Searching..." />
      ) : (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.15 }}
          className="space-y-3"
        >
          <p className="text-xs text-slate-500">{acts.length} results found</p>
          {acts.map((act, i) => (
            <motion.div
              key={act.adam}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: Math.min(i * 0.03, 0.3) }}
            >
              <Link
                to={`/act/${act.adam}`}
                className="block bg-white rounded-xl border border-slate-200 p-4 hover:shadow-md hover:border-slate-300 transition-all duration-200 group"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium ${getActTypeColor(act.type)}`}>
                        {getActTypeLabel(act.type)}
                      </span>
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium ${getStatusColor(act.status)}`}>
                        {getStatusLabel(act.status)}
                      </span>
                      <span className="text-xs text-slate-400 font-mono">{act.adam}</span>
                    </div>
                    <h3 className="text-sm font-medium text-slate-800 line-clamp-2 group-hover:text-teal-700 transition-colors">
                      {act.title || 'Untitled'}
                    </h3>
                    <div className="flex items-center gap-4 mt-2 text-xs text-slate-500">
                      {act.submission_date && (
                        <span className="flex items-center gap-1">
                          <Calendar className="w-3 h-3" />
                          {formatDate(act.submission_date)}
                        </span>
                      )}
                      {act.city && (
                        <span className="flex items-center gap-1">
                          <MapPin className="w-3 h-3" />
                          {act.city}
                        </span>
                      )}
                      {act.contract_type && (
                        <span>{getContractTypeLabel(act.contract_type)}</span>
                      )}
                    </div>
                  </div>
                  <div className="text-right flex-shrink-0">
                    {(act.cost_without_vat || act.budget) && (
                      <p className="text-sm font-semibold text-slate-900">
                        {formatCurrency(act.cost_without_vat || act.budget)}
                      </p>
                    )}
                    {act.cost_without_vat && (
                      <p className="text-xs text-slate-400 mt-0.5">excl. VAT</p>
                    )}
                  </div>
                </div>
              </Link>
            </motion.div>
          ))}

          {acts.length === 0 && (
            <div className="text-center py-12">
              <SearchIcon className="w-10 h-10 text-slate-300 mx-auto mb-3" />
              <p className="text-sm text-slate-500">No results found</p>
              <p className="text-xs text-slate-400 mt-1">Try adjusting your search or filters</p>
            </div>
          )}
        </motion.div>
      )}
    </div>
  )
}
