import { useState, useEffect } from 'react'
import { supabase } from '../lib/supabase'

export function useQuery<T>(
  tableName: string,
  options?: {
    select?: string
    filters?: Record<string, unknown>
    order?: { column: string; ascending?: boolean }
    limit?: number
    search?: { column: string; value: string }
  }
) {
  const [data, setData] = useState<T[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const deps = JSON.stringify(options)

  useEffect(() => {
    async function fetchData() {
      setLoading(true)
      setError(null)

      let query = supabase
        .from(tableName)
        .select(options?.select || '*')

      if (options?.filters) {
        for (const [key, value] of Object.entries(options.filters)) {
          if (value !== undefined && value !== null && value !== '') {
            query = query.eq(key, value)
          }
        }
      }

      if (options?.search?.value) {
        query = query.ilike(options.search.column, `%${options.search.value}%`)
      }

      if (options?.order) {
        query = query.order(options.order.column, {
          ascending: options.order.ascending ?? false,
        })
      }

      if (options?.limit) {
        query = query.limit(options.limit)
      }

      const { data: result, error: err } = await query

      if (err) {
        setError(err.message)
        setData(null)
      } else {
        setData(result as T[])
      }
      setLoading(false)
    }

    fetchData()
  }, [tableName, deps])

  return { data, loading, error }
}
