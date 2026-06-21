import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  ArrowLeft, Calendar, MapPin, Building2, Tag, FileText, Euro,
  Clock, ExternalLink,
} from 'lucide-react'
import { supabase } from '../lib/supabase'
import {
  formatFullCurrency, formatDate, formatDateTime,
  getActTypeLabel, getActTypeColor, getStatusColor, getStatusLabel,
  getContractTypeLabel, getProcedureTypeLabel,
} from '../lib/format'
import LoadingSpinner from '../components/LoadingSpinner'
import type { ProcurementAct, Authority, Contractor, ActAward } from '../lib/types'

export default function ActDetail() {
  const { adam } = useParams<{ adam: string }>()
  const [act, setAct] = useState<ProcurementAct | null>(null)
  const [authority, setAuthority] = useState<Authority | null>(null)
  const [awards, setAwards] = useState<(ActAward & { contractor?: Contractor })[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!adam) return
    async function load() {
      const { data: actData } = await supabase
        .from('procurement_acts')
        .select('*')
        .eq('adam', adam)
        .maybeSingle()

      if (actData) {
        setAct(actData)
        if (actData.authority_id) {
          const { data: auth } = await supabase
            .from('authorities')
            .select('*')
            .eq('id', actData.authority_id)
            .maybeSingle()
          if (auth) setAuthority(auth)
        }

        const { data: awardsData } = await supabase
          .from('act_awards')
          .select('*')
          .eq('act_adam', adam)

        if (awardsData && awardsData.length > 0) {
          const contractorIds = awardsData.map(a => a.contractor_id)
          const { data: contractors } = await supabase
            .from('contractors')
            .select('*')
            .in('id', contractorIds)

          const enriched = awardsData.map(award => ({
            ...award,
            contractor: contractors?.find(c => c.id === award.contractor_id),
          }))
          setAwards(enriched)
        }
      }
      setLoading(false)
    }
    load()
  }, [adam])

  if (loading) return <LoadingSpinner text="Loading act details..." />

  if (!act) {
    return (
      <div className="text-center py-16">
        <FileText className="w-12 h-12 text-slate-300 mx-auto mb-4" />
        <h2 className="text-lg font-semibold text-slate-700">Act not found</h2>
        <p className="text-sm text-slate-500 mt-1">No procurement act with ADAM: {adam}</p>
        <Link to="/search" className="inline-flex items-center gap-2 mt-4 text-sm text-teal-600 hover:text-teal-700">
          <ArrowLeft className="w-4 h-4" /> Back to search
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <Link to="/search" className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-teal-700 transition-colors mb-4">
          <ArrowLeft className="w-4 h-4" /> Back to search
        </Link>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.05 }}
        className="bg-white rounded-xl border border-slate-200 overflow-hidden"
      >
        <div className="p-6 border-b border-slate-100">
          <div className="flex items-center gap-2 mb-3">
            <span className={`inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium ${getActTypeColor(act.type)}`}>
              {getActTypeLabel(act.type)}
            </span>
            <span className={`inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium ${getStatusColor(act.status)}`}>
              {getStatusLabel(act.status)}
            </span>
          </div>
          <h1 className="text-xl font-bold text-slate-900 leading-snug">
            {act.title || 'Untitled Act'}
          </h1>
          <p className="text-sm text-slate-500 font-mono mt-2">ADAM: {act.adam}</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-px bg-slate-100">
          <InfoCell icon={Euro} label="Cost (excl. VAT)" value={formatFullCurrency(act.cost_without_vat)} />
          <InfoCell icon={Euro} label="Cost (incl. VAT)" value={formatFullCurrency(act.cost_with_vat)} />
          <InfoCell icon={Euro} label="Budget" value={formatFullCurrency(act.budget)} />
          <InfoCell icon={Calendar} label="Signed Date" value={formatDate(act.signed_date)} />
          <InfoCell icon={Clock} label="Submitted" value={formatDateTime(act.submission_date)} />
          <InfoCell icon={Clock} label="Deadline" value={formatDateTime(act.final_submission_date)} />
          <InfoCell icon={Tag} label="Contract Type" value={getContractTypeLabel(act.contract_type)} />
          <InfoCell icon={Tag} label="Procedure" value={getProcedureTypeLabel(act.procedure_type)} />
          <InfoCell icon={MapPin} label="Location" value={act.city || '-'} />
        </div>
      </motion.div>

      {authority && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15 }}
          className="bg-white rounded-xl border border-slate-200 p-6"
        >
          <div className="flex items-center gap-2 mb-3">
            <Building2 className="w-4 h-4 text-slate-400" />
            <h2 className="text-sm font-semibold text-slate-700">Contracting Authority</h2>
          </div>
          <div className="space-y-2">
            <p className="text-base font-medium text-slate-900">{authority.name}</p>
            <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-500">
              {authority.vat_number && <span>VAT: {authority.vat_number}</span>}
              {authority.city && <span>{authority.city}</span>}
              {authority.nuts_code && <span>NUTS: {authority.nuts_code}</span>}
            </div>
          </div>
        </motion.div>
      )}

      {awards.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25 }}
          className="bg-white rounded-xl border border-slate-200 p-6"
        >
          <h2 className="text-sm font-semibold text-slate-700 mb-4">Award Information</h2>
          <div className="space-y-3">
            {awards.map(award => (
              <div key={award.id} className="flex items-center justify-between p-3 bg-slate-50 rounded-lg">
                <div>
                  <p className="text-sm font-medium text-slate-800">
                    {award.contractor?.name || `Contractor #${award.contractor_id}`}
                  </p>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {award.contractor?.vat_number && `VAT: ${award.contractor.vat_number}`}
                    {award.role && ` | Role: ${award.role}`}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-sm font-semibold text-slate-900">
                    {formatFullCurrency(award.awarded_value)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </motion.div>
      )}

      {act.cpv_main && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="bg-white rounded-xl border border-slate-200 p-6"
        >
          <h2 className="text-sm font-semibold text-slate-700 mb-3">Classification</h2>
          <div className="flex items-center gap-2">
            <span className="text-xs bg-slate-100 text-slate-700 px-2 py-1 rounded font-mono">
              CPV: {act.cpv_main}
            </span>
            {act.nuts_code && (
              <span className="text-xs bg-slate-100 text-slate-700 px-2 py-1 rounded font-mono">
                NUTS: {act.nuts_code}
              </span>
            )}
          </div>
        </motion.div>
      )}
    </div>
  )
}

function InfoCell({ icon: Icon, label, value }: { icon: typeof Euro; label: string; value: string }) {
  return (
    <div className="bg-white p-4">
      <div className="flex items-center gap-1.5 mb-1">
        <Icon className="w-3.5 h-3.5 text-slate-400" />
        <span className="text-xs text-slate-500">{label}</span>
      </div>
      <p className="text-sm font-medium text-slate-800">{value}</p>
    </div>
  )
}
