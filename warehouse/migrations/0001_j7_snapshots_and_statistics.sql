-- =============================================================================
-- Brings a warehouse created before J7 to the state that init/ now creates.
--
-- init/ scripts only run when the data volume is EMPTY (first start). A
-- warehouse that already exists needs its changes applied by hand, once:
--     make migrate-warehouse
-- Every statement can be run again without harm.
-- =============================================================================

-- History kept by dbt snapshots (03_transform.sh).
CREATE SCHEMA IF NOT EXISTS snapshots AUTHORIZATION transformer;

-- The extractor refreshes the statistics of the tables it loads (02_warehouse.sql).
-- SQLFluff does not know MAINTAIN yet (PostgreSQL 17): its parse error is ignored.
GRANT MAINTAIN ON ALL TABLES IN SCHEMA raw TO extractor; -- noqa: PRS

-- Tables that never change (line stops, stations...) will not be reloaded,
-- so the extractor will not analyze them: do it once here.
ANALYZE;
