-- An incremental model must not forget anything: every ticket of the
-- source is in fct_tickets. (The reconciliation test checks the amounts;
-- this one checks that no row is missing.)
SELECT t.ticket_id
FROM {{ ref('stg_tickets') }} AS t
LEFT JOIN {{ ref('fct_tickets') }} AS f ON t.ticket_id = f.ticket_id
WHERE f.ticket_id IS NULL
