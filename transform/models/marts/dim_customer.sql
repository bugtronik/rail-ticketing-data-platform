-- One row per VERSION of a customer (SCD type 2), from the snapshot.
-- A sale points to the version that was valid when the order was placed:
-- a GOLD customer who was SILVER at the time is counted as SILVER.
--
-- No personal data: the dashboard analyses segments, not people.
-- Row -1 stands for anonymous purchases, so that every ticket has a customer
-- and BI tools never show a blank member.
WITH versions AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY dbt_valid_from) AS version_number,
        DATE_PART('year', AGE(CURRENT_DATE, birth_date))::INT AS age
    FROM {{ ref('snap_customers') }}
)

SELECT
    dbt_scd_id AS customer_sk,
    customer_id,
    version_number,
    city,
    loyalty_tier,
    CASE
        WHEN age < 25 THEN '18-24'
        WHEN age < 35 THEN '25-34'
        WHEN age < 50 THEN '35-49'
        WHEN age < 65 THEN '50-64'
        ELSE '65+'
    END AS age_band,
    created_at_local::DATE AS customer_since,
    -- The snapshot only knows the changes made since it started. The first
    -- version is therefore stretched back to the beginning: before the
    -- snapshot existed, the current value is the best information we have.
    CASE
        WHEN version_number = 1 THEN '1900-01-01'::TIMESTAMP
        ELSE {{ to_local_time('dbt_valid_from') }}
    END AS valid_from,
    CASE
        WHEN dbt_valid_to = '9999-12-31'::TIMESTAMP THEN dbt_valid_to
        ELSE {{ to_local_time('dbt_valid_to') }}
    END AS valid_to,
    dbt_valid_to = '9999-12-31'::TIMESTAMP AS is_current
FROM versions

UNION ALL

SELECT
    '-1' AS customer_sk,
    -1 AS customer_id,
    1 AS version_number,
    'Inconnue' AS city,
    'ANONYME' AS loyalty_tier,
    'Inconnu' AS age_band,
    NULL AS customer_since,
    '1900-01-01'::TIMESTAMP AS valid_from,
    '9999-12-31'::TIMESTAMP AS valid_to,
    TRUE AS is_current
