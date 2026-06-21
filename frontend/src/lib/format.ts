import type { ActType } from './types'

export function formatCurrency(value: number | null | undefined): string {
  if (value == null) return '-'
  if (value >= 1_000_000_000) {
    return `${(value / 1_000_000_000).toFixed(1)} B`
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)} M`
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)} K`
  }
  return value.toLocaleString('el-GR')
}

export function formatFullCurrency(value: number | null | undefined): string {
  if (value == null) return '-'
  return new Intl.NumberFormat('el-GR', {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value)
}

export function formatDate(date: string | null | undefined): string {
  if (!date) return '-'
  return new Date(date).toLocaleDateString('el-GR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })
}

export function formatDateTime(date: string | null | undefined): string {
  if (!date) return '-'
  return new Date(date).toLocaleDateString('el-GR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function getActTypeLabel(type: ActType): string {
  const labels: Record<ActType, string> = {
    request: 'Αίτημα',
    notice: 'Προκήρυξη',
    auction: 'Κατακύρωση',
    contract: 'Σύμβαση',
    payment: 'Πληρωμή',
  }
  return labels[type]
}

export function getActTypeColor(type: ActType): string {
  const colors: Record<ActType, string> = {
    request: 'bg-slate-100 text-slate-700',
    notice: 'bg-teal-50 text-teal-700',
    auction: 'bg-amber-50 text-amber-700',
    contract: 'bg-emerald-50 text-emerald-700',
    payment: 'bg-sky-50 text-sky-700',
  }
  return colors[type]
}

export function getStatusColor(status: string): string {
  switch (status) {
    case 'active': return 'bg-green-50 text-green-700'
    case 'cancelled': return 'bg-red-50 text-red-700'
    case 'modified': return 'bg-orange-50 text-orange-700'
    default: return 'bg-gray-50 text-gray-700'
  }
}

export function getStatusLabel(status: string): string {
  switch (status) {
    case 'active': return 'Ενεργή'
    case 'cancelled': return 'Ακυρωμένη'
    case 'modified': return 'Τροποποιημένη'
    default: return status
  }
}

export function getContractTypeLabel(type: string | null): string {
  if (!type) return '-'
  const labels: Record<string, string> = {
    supplies: 'Προμήθειες',
    services: 'Υπηρεσίες',
    works: 'Έργα',
  }
  return labels[type] || type
}

export function getProcedureTypeLabel(type: string | null): string {
  if (!type) return '-'
  const labels: Record<string, string> = {
    open: 'Ανοικτή',
    restricted: 'Κλειστή',
    simplified: 'Απλοποιημένη',
    competitive_dialogue: 'Ανταγωνιστικός Διάλογος',
    design_contest: 'Αρχιτεκτονικός Διαγωνισμός',
  }
  return labels[type] || type
}
