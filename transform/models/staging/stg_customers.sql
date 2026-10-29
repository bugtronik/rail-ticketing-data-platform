-- Data minimisation: names, email and phone stop here. Nothing downstream
-- needs them, so they never reach a schema that BI tools can read.
SELECT
    customer_id,
    city,
    loyalty_tier,
    birth_date,
    is_active,
    {{ to_local_time('created_at') }} AS created_at_local,
    -- UTC, as in the source: the snapshot dates each version with it.
    updated_at
FROM {{ source('raw', 'customers') }}
