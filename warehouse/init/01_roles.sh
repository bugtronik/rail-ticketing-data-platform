# shellcheck shell=bash
# =============================================================================
# Warehouse roles, created once at first container start.
#
# Not executable on purpose: the image's entrypoint then *sources* it (same
# convention as the MariaDB init scripts).
#
# The password is passed to psql as a variable (-v) and inserted with :'pw',
# which psql quotes safely: no SQL injection, even with a quote in the
# password. Building the SQL with shell interpolation would not be safe.
# =============================================================================

: "${EXTRACTOR_DB_PASSWORD:?EXTRACTOR_DB_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v pw="$EXTRACTOR_DB_PASSWORD" <<'EOSQL'
-- The extractor writes the raw layer and its own bookkeeping, nothing else.
CREATE ROLE extractor LOGIN PASSWORD :'pw';
EOSQL
