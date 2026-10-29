-- Grain: one ticket. The main fact table of the dashboard.
--
-- Two dates, two questions:
--   order_date      when it was SOLD: "ventes" (the commercial team's main view)
--   departure_date  when the passenger TRAVELS: realised revenue, load factor
-- Refunds stay attached to the original sale (net_sales_amount), so a period's
-- net sales can still move when its tickets are refunded later.
--
-- INCREMENTAL: each run rebuilds only the tickets whose inputs changed since
-- the previous run, instead of 2.5 M rows. A ticket's row depends on the
-- ticket, its order, its trip and its refunds: a change to any of them (a
-- cancellation, a payment, a refund paid months later) re-processes it.
-- Changes are found with _extracted_at, the time each row version reached
-- the warehouse. `dbt build --full-refresh` rebuilds the whole table.
{{
    config(
        materialized='incremental',
        unique_key='ticket_id',
        incremental_strategy='delete+insert',
        on_schema_change='fail',
        indexes=[{'columns': ['ticket_id'], 'unique': True}]
    )
}}

-- stg_refunds is only read inside the incremental block below. dbt finds
-- dependencies when it parses the file, with is_incremental() false: this
-- hint declares the one it cannot see.
-- depends_on: {{ ref('stg_refunds') }}

{% if is_incremental() %}
    {#-
        Where to start, read from this table BEFORE writing the query, so the
        bounds reach PostgreSQL as constants. With constants, the planner
        uses its statistics and knows that a few thousand tickets changed.
        With a subquery, it cannot know the values in advance, expects
        millions of rows and picks a plan that made the incremental run
        slower than a full rebuild (measured: 110 s against 20 s).

        changed_after: same idea as the extractor's overlap. A load that
        committed late can carry an _extracted_at slightly older than what
        this table already holds; re-processing an hour of changes is cheap
        and harmless (delete+insert on ticket_id).
        first_recent_order: the last day of orders is always
        re-processed. A customer's new segment can reach the snapshot one run
        after an order placed just after the change. Order ids grow with
        time, so "order_id >= N" selects them, and on a column the planner
        has statistics for.
    -#}
    {%- set bounds_query -%}
        SELECT
            MAX(_loaded_at) - INTERVAL '{{ var("incremental_lookback") }}' AS changed_after,
            MIN(order_id) FILTER (
                WHERE order_date >= (SELECT MAX(order_date) - 1 FROM {{ this }})
            ) AS first_recent_order
        FROM {{ this }}
    {%- endset -%}
    {%- if execute -%}
        {%- set bounds = run_query(bounds_query).rows[0] -%}
        {%- set changed_after = "'" ~ bounds[0] ~ "'::TIMESTAMPTZ" -%}
        {%- set first_recent_order = bounds[1] -%}
    {%- else -%}
        {%- set changed_after = "NOW()" -%}
        {%- set first_recent_order = 0 -%}
    {%- endif %}

    WITH touched AS (
        SELECT t.ticket_id
        FROM {{ ref('stg_tickets') }} AS t
        WHERE t._extracted_at > {{ changed_after }}

        UNION

        SELECT t.ticket_id
        FROM {{ ref('stg_tickets') }} AS t
        WHERE t.order_id >= {{ first_recent_order }}

        UNION

        SELECT t.ticket_id
        FROM {{ ref('stg_tickets') }} AS t
        INNER JOIN {{ ref('stg_orders') }} AS o ON t.order_id = o.order_id
        WHERE o._extracted_at > {{ changed_after }}

        UNION

        SELECT t.ticket_id
        FROM {{ ref('stg_tickets') }} AS t
        INNER JOIN {{ ref('stg_trips') }} AS tr ON t.trip_id = tr.trip_id
        WHERE tr._extracted_at > {{ changed_after }}

        UNION

        SELECT r.ticket_id
        FROM {{ ref('stg_refunds') }} AS r
        WHERE r._extracted_at > {{ changed_after }}
    )
{% endif %}

SELECT
    t.ticket_id,
    t.order_id,
    t.trip_id,
    tr.line_id,
    t.origin_station_id,
    t.destination_station_id,
    o.channel_id,
    COALESCE(o.customer_id, -1) AS customer_id,
    -- The customer's version (segment) at the time of the order.
    COALESCE(c.customer_sk, '-1') AS customer_sk,
    o.ordered_at_local::DATE AS order_date,
    EXTRACT(HOUR FROM o.ordered_at_local)::INT AS order_hour,
    tr.departure_at_local::DATE AS departure_date,
    t.fare_class,
    t.fare_type,
    t.passenger_type,
    t.ticket_status,
    s.is_paid,
    s.is_cancelled,
    s.is_used,
    s.occupies_seat,
    t.price AS ticket_price,
    CASE WHEN s.is_paid THEN t.price ELSE 0 END AS sales_amount,
    COALESCE(r.refunded_amount, 0) AS refunded_amount,
    CASE WHEN s.is_paid THEN t.price ELSE 0 END
    - COALESCE(r.refunded_amount, 0) AS net_sales_amount,
    j.distance_km,
    CASE WHEN s.occupies_seat THEN j.distance_km ELSE 0 END AS passenger_km,
    -- The latest arrival among the row's inputs: the next run starts there.
    GREATEST(t._extracted_at, o._extracted_at, tr._extracted_at, r._extracted_at) AS _loaded_at
FROM {{ ref('stg_tickets') }} AS t
INNER JOIN {{ ref('stg_orders') }} AS o ON t.order_id = o.order_id
INNER JOIN {{ ref('stg_trips') }} AS tr ON t.trip_id = tr.trip_id
INNER JOIN {{ ref('int_ticket_status') }} AS s ON t.ticket_id = s.ticket_id
INNER JOIN {{ ref('int_ticket_journeys') }} AS j ON t.ticket_id = j.ticket_id
LEFT JOIN {{ ref('int_ticket_refunds') }} AS r ON t.ticket_id = r.ticket_id
LEFT JOIN {{ ref('dim_customer') }} AS c
    ON
        o.customer_id = c.customer_id
        AND o.ordered_at_local >= c.valid_from
        AND o.ordered_at_local < c.valid_to
{% if is_incremental() %}
    WHERE t.ticket_id IN (SELECT touched.ticket_id FROM touched)
{% endif %}
