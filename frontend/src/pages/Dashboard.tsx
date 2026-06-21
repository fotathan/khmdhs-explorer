import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  AreaChart, Area, PieChart, Pie, Cell,
} from 'recharts'
import {
  FileText, Building2, Users, TrendingUp, ArrowRight,
  Activity, DollarSign,
} from 'lucide-react'
import { supabase } from '../lib/supabase'
import { formatCurrency } from '../lib/format'
import StatCard from '../components/StatCard'
import LoadingSpinner from '../components/LoadingSpinner'
import type { AnalyticsSummary, Authority, Contractor } from '../lib/types'

const PIE_COLORS = ['#0F766E', '#14B8A6', '#2DD4BF', '#5EEAD4', '#99F6E4', '#CCFBF1']

export default function Dashboard() {
  const [analytics, setAnalytics] = useState<AnalyticsSummary[]>([])
  const [topAuthorities, setTopAuthorities] = useState<Authority[]>([])
  const [topContractors, setTopContractors] = useState<Contractor[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function loadData() {
      const [analyticsRes, authRes, contractorRes] = await Promise.all([
        supabase.from('analytics_summary').select('*'),
        supabase.from('authorities').select('*').order('total_value', { ascending: false }).limit(5),
        supabase.from('contractors').select('*').order('total_value', { ascending: false }).limit(5),
      ])
      if (analyticsRes.data) setAnalytics(analyticsRes.data)
      if (authRes.data) setTopAuthorities(authRes.data)
      if (contractorRes.data) setTopContractors(contractorRes.data)
      setLoading(false)
    }
    loadData()
  }, [])

  if (loading) return <LoadingSpinner text="Loading dashboard..." />

  const overviewMetrics = analytics.filter(a => a.category === 'overview')
  const totalContracts = overviewMetrics.find(m => m.metric_name === 'total_contracts')?.metric_value ?? 0
  const totalValue = overviewMetrics.find(m => m.metric_name === 'total_value')?.metric_value ?? 0
  const totalAuthorities = overviewMetrics.find(m => m.metric_name === 'total_authorities')?.metric_value ?? 0
  const totalContractors = overviewMetrics.find(m => m.metric_name === 'total_contractors')?.metric_value ?? 0

  const monthlyContracts = analytics
    .filter(a => a.metric_name === 'monthly_contracts' && a.period)
    .sort((a, b) => (a.period! > b.period! ? 1 : -1))
    .map(a => ({
      month: a.period!.slice(5),
      contracts: a.metric_value,
    }))

  const monthlyValues = analytics
    .filter(a => a.metric_name === 'monthly_value' && a.period)
    .sort((a, b) => (a.period! > b.period! ? 1 : -1))
    .map(a => ({
      month: a.period!.slice(5),
      value: (a.metric_value ?? 0) / 1_000_000_000,
    }))

  const cpvData = analytics
    .filter(a => a.metric_name === 'cpv_value')
    .sort((a, b) => (b.metric_value ?? 0) - (a.metric_value ?? 0))
    .map(a => ({
      name: a.category ?? '',
      value: (a.metric_value ?? 0) / 1_000_000_000,
    }))

  return (
    <div className="space-y-8">
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <h1 className="text-2xl font-bold text-slate-900">Dashboard</h1>
        <p className="text-sm text-slate-500 mt-1">
          Overview of Greek public procurement activity
        </p>
      </motion.div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          icon={FileText}
          label="Total Acts"
          value={formatCurrency(totalContracts)}
          subtitle="All procurement acts"
          color="bg-teal-50 text-teal-600"
          delay={0}
        />
        <StatCard
          icon={DollarSign}
          label="Total Value"
          value={`${formatCurrency(totalValue)}`}
          subtitle="Awarded contract value"
          color="bg-emerald-50 text-emerald-600"
          delay={0.1}
        />
        <StatCard
          icon={Building2}
          label="Authorities"
          value={formatCurrency(totalAuthorities)}
          subtitle="Contracting bodies"
          color="bg-sky-50 text-sky-600"
          delay={0.2}
        />
        <StatCard
          icon={Users}
          label="Contractors"
          value={formatCurrency(totalContractors)}
          subtitle="Economic operators"
          color="bg-amber-50 text-amber-600"
          delay={0.3}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="bg-white rounded-xl border border-slate-200 p-6"
        >
          <div className="flex items-center justify-between mb-6">
            <div>
              <h2 className="text-base font-semibold text-slate-900">Monthly Contracts</h2>
              <p className="text-xs text-slate-500 mt-0.5">Number of new contracts per month</p>
            </div>
            <Activity className="w-4 h-4 text-slate-400" />
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={monthlyContracts} barSize={24}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
              <XAxis dataKey="month" tick={{ fontSize: 11 }} stroke="#94A3B8" />
              <YAxis tick={{ fontSize: 11 }} stroke="#94A3B8" />
              <Tooltip
                contentStyle={{
                  borderRadius: 8,
                  border: '1px solid #E2E8F0',
                  boxShadow: '0 4px 6px -1px rgba(0,0,0,0.1)',
                }}
              />
              <Bar dataKey="contracts" fill="#0F766E" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="bg-white rounded-xl border border-slate-200 p-6"
        >
          <div className="flex items-center justify-between mb-6">
            <div>
              <h2 className="text-base font-semibold text-slate-900">Monthly Value</h2>
              <p className="text-xs text-slate-500 mt-0.5">Total value in billions EUR</p>
            </div>
            <TrendingUp className="w-4 h-4 text-slate-400" />
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={monthlyValues}>
              <defs>
                <linearGradient id="valueGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#14B8A6" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="#14B8A6" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
              <XAxis dataKey="month" tick={{ fontSize: 11 }} stroke="#94A3B8" />
              <YAxis tick={{ fontSize: 11 }} stroke="#94A3B8" unit="B" />
              <Tooltip
                contentStyle={{
                  borderRadius: 8,
                  border: '1px solid #E2E8F0',
                  boxShadow: '0 4px 6px -1px rgba(0,0,0,0.1)',
                }}
                formatter={(value) => [`${Number(value).toFixed(2)}B EUR`, 'Value']}
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke="#14B8A6"
                strokeWidth={2}
                fill="url(#valueGradient)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </motion.div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="bg-white rounded-xl border border-slate-200 p-6"
        >
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-base font-semibold text-slate-900">CPV Sectors</h2>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie
                data={cpvData}
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={80}
                paddingAngle={2}
                dataKey="value"
              >
                {cpvData.map((_, idx) => (
                  <Cell key={idx} fill={PIE_COLORS[idx % PIE_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ borderRadius: 8, border: '1px solid #E2E8F0' }}
                formatter={(value) => [`${Number(value).toFixed(1)}B EUR`]}
              />
            </PieChart>
          </ResponsiveContainer>
          <div className="mt-2 space-y-1.5">
            {cpvData.slice(0, 4).map((item, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <div
                  className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
                  style={{ backgroundColor: PIE_COLORS[i] }}
                />
                <span className="text-slate-600 truncate">{item.name}</span>
                <span className="ml-auto text-slate-400 font-medium">{item.value.toFixed(1)}B</span>
              </div>
            ))}
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          className="bg-white rounded-xl border border-slate-200 p-6"
        >
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-base font-semibold text-slate-900">Top Authorities</h2>
            <Link
              to="/authorities"
              className="text-xs text-teal-600 hover:text-teal-700 font-medium flex items-center gap-1 transition-colors"
            >
              View all <ArrowRight className="w-3 h-3" />
            </Link>
          </div>
          <div className="space-y-3">
            {topAuthorities.map((auth, i) => (
              <div key={auth.id} className="group">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-slate-700 truncate max-w-[180px] group-hover:text-teal-700 transition-colors">
                    {auth.name}
                  </span>
                  <span className="text-xs text-slate-500 font-medium">
                    {formatCurrency(auth.total_value)}
                  </span>
                </div>
                <div className="mt-1.5 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                  <motion.div
                    initial={{ width: 0 }}
                    animate={{ width: `${((auth.total_value || 0) / (topAuthorities[0]?.total_value || 1)) * 100}%` }}
                    transition={{ duration: 0.8, delay: 0.5 + i * 0.1 }}
                    className="h-full bg-teal-500 rounded-full"
                  />
                </div>
              </div>
            ))}
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.6 }}
          className="bg-white rounded-xl border border-slate-200 p-6"
        >
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-base font-semibold text-slate-900">Top Contractors</h2>
            <Link
              to="/contractors"
              className="text-xs text-teal-600 hover:text-teal-700 font-medium flex items-center gap-1 transition-colors"
            >
              View all <ArrowRight className="w-3 h-3" />
            </Link>
          </div>
          <div className="space-y-3">
            {topContractors.map((contractor, i) => (
              <div key={contractor.id} className="group">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-slate-700 truncate max-w-[180px] group-hover:text-teal-700 transition-colors">
                    {contractor.name}
                  </span>
                  <span className="text-xs text-slate-500 font-medium">
                    {formatCurrency(contractor.total_value)}
                  </span>
                </div>
                <div className="mt-1.5 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                  <motion.div
                    initial={{ width: 0 }}
                    animate={{ width: `${((contractor.total_value || 0) / (topContractors[0]?.total_value || 1)) * 100}%` }}
                    transition={{ duration: 0.8, delay: 0.6 + i * 0.1 }}
                    className="h-full bg-emerald-500 rounded-full"
                  />
                </div>
              </div>
            ))}
          </div>
        </motion.div>
      </div>
    </div>
  )
}
