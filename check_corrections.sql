cd /Users/fotathan/PythonApps/KHMDHS
cat > check_corrections.sql << 'EOF'
SELECT count(*) AS n_corrected
FROM proc.v_act_annotation_current
WHERE corrected_value IS NOT NULL;

SELECT count(*) AS n_annotations FROM proc.v_act_annotation_current;

SELECT count(*) AS n_acts FROM proc.procurement_act;
EOF