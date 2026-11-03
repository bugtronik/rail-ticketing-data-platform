# shellcheck shell=bash
# =============================================================================
# Power BI's account: reads the marts schema, nothing else. No raw data, no
# personal data, no write access.
#
# SELECT on the tables themselves is granted by dbt (`grants` in
# transform/dbt_project.yml): dbt recreates a table at each run, and a GRANT
# given here would disappear with the old table.
#
# Runs at first start, and on an existing warehouse through
# migrations/0002_j8_bi_reader.sh: it can run twice without harm (the role
# is only created if it is missing). Same conventions as 01_roles.sh.
#
# The connection limit is set apart, on every run: changing it here and
# running `make migrate-warehouse` updates an existing role too. 20 leaves
# room for Power BI, which loads several tables in parallel (one connection
# each) while its editor keeps a few open for the previews.
# =============================================================================

: "${BI_READER_PASSWORD:?BI_READER_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v pw="$BI_READER_PASSWORD" <<'EOSQL'
-- \gexec runs the statement built by the SELECT, if the SELECT returns a row.
SELECT format('CREATE ROLE bi_reader LOGIN PASSWORD %L', :'pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bi_reader')
\gexec

ALTER ROLE bi_reader CONNECTION LIMIT 20;

GRANT USAGE ON SCHEMA marts TO bi_reader;
EOSQL
