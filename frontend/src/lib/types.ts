export type ActType = 'request' | 'notice' | 'auction' | 'contract' | 'payment'
export type ActStatus = 'active' | 'cancelled' | 'modified'

export interface ProcurementAct {
  adam: string
  type: ActType
  title: string | null
  authority_id: string | null
  signed_date: string | null
  submission_date: string | null
  final_submission_date: string | null
  contract_type: string | null
  procedure_type: string | null
  status: ActStatus
  budget: number | null
  cost_without_vat: number | null
  cost_with_vat: number | null
  currency: string
  nuts_code: string | null
  city: string | null
  cpv_main: string | null
  created_at: string
}

export interface Authority {
  id: string
  name: string
  vat_number: string | null
  nuts_code: string | null
  city: string | null
  postal_code: string | null
  country: string
  act_count: number
  total_value: number
  created_at: string
}

export interface Contractor {
  id: number
  name: string
  vat_number: string | null
  country: string
  act_count: number
  total_value: number
  created_at: string
}

export interface CpvCode {
  code: string
  description: string
}

export interface ActAward {
  id: number
  act_adam: string
  contractor_id: number
  awarded_value: number | null
  role: string
}

export interface AnalyticsSummary {
  id: number
  metric_name: string
  metric_value: number | null
  period: string | null
  category: string | null
  updated_at: string
}
