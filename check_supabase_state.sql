cat > check_supabase_state.sql << 'EOF'
SELECT 'search_tsv column' AS feature,
       EXISTS(SELECT 1 FROM information_schema.columns
              WHERE table_schema='proc' AND table_name='procurement_act'
              AND column_name='search_tsv') AS present
UNION ALL
SELECT 'origin column',
       EXISTS(SELECT 1 FROM information_schema.columns
              WHERE table_schema='proc' AND table_name='procurement_act'
              AND column_name='origin')
UNION ALL
SELECT 'external_id column',
       EXISTS(SELECT 1 FROM information_schema.columns
              WHERE table_schema='proc' AND table_name='procurement_act'
              AND column_name='external_id')
UNION ALL
SELECT 'mv_explore_authority matview',
       EXISTS(SELECT 1 FROM pg_matviews
              WHERE schemaname='proc' AND matviewname='mv_explore_authority')
UNION ALL
SELECT 'cpv description_tsv column',
       EXISTS(SELECT 1 FROM information_schema.columns
              WHERE table_schema='proc' AND table_name='cpv_code'
              AND column_name='description_tsv');
EOF