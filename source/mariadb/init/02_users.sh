# shellcheck shell=bash
# =============================================================================
# Least-privilege database accounts, created once at first container start.
#
# This file must NOT be executable: the image's entrypoint then *sources* it,
# which gives it access to the entrypoint's helper functions
# (docker_process_sql, docker_sql_escape_string_literal). An executable .sh
# would run in a separate process, without those helpers.
#
# Passwords come from environment variables (set in .env, never committed).
# =============================================================================

: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set}"
: "${ETL_DB_PASSWORD:?ETL_DB_PASSWORD must be set}"

app_password="$(docker_sql_escape_string_literal "${APP_DB_PASSWORD}")"
etl_password="$(docker_sql_escape_string_literal "${ETL_DB_PASSWORD}")"

docker_process_sql <<EOSQL
-- The application (our data generator) reads and writes, but cannot DELETE:
-- the "no physical delete" rule of the schema is enforced by privileges,
-- not by goodwill. Without it, the watermark extraction would miss deletions.
CREATE USER 'ticketing_app'@'%' IDENTIFIED BY '${app_password}';
GRANT SELECT, INSERT, UPDATE ON \`${MARIADB_DATABASE}\`.* TO 'ticketing_app'@'%';

-- The extractor only reads. Resource limits protect production:
--   * at most 2 simultaneous connections;
--   * any statement running more than 60 s is killed by the server.
CREATE USER 'etl_reader'@'%' IDENTIFIED BY '${etl_password}'
    WITH MAX_USER_CONNECTIONS 2 MAX_STATEMENT_TIME 60;
GRANT SELECT ON \`${MARIADB_DATABASE}\`.* TO 'etl_reader'@'%';
EOSQL
