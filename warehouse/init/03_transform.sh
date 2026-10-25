# shellcheck shell=bash
# =============================================================================
# dbt's role and schemas, created once at first container start.
#
# `transformer` (the account dbt connects with) reads raw and meta, and owns
# the schemas it builds: staging, intermediate, marts. It cannot write raw:
# only the extractor can. Each tool gets exactly the rights its job needs.
#
# Same conventions as 01_roles.sh: sourced (not executable), password passed
# to psql as a variable.
# =============================================================================

: "${TRANSFORMER_DB_PASSWORD:?TRANSFORMER_DB_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v pw="$TRANSFORMER_DB_PASSWORD" <<'EOSQL'
CREATE ROLE transformer LOGIN PASSWORD :'pw';

-- Read the extracted data and the extraction log (for source freshness).
GRANT USAGE ON SCHEMA raw, meta TO transformer;
GRANT SELECT ON ALL TABLES IN SCHEMA raw TO transformer;
GRANT SELECT ON meta.extract_runs, meta.extract_state TO transformer;

-- The layers dbt builds, owned by dbt.
CREATE SCHEMA staging AUTHORIZATION transformer;
CREATE SCHEMA intermediate AUTHORIZATION transformer;
CREATE SCHEMA marts AUTHORIZATION transformer;
EOSQL
