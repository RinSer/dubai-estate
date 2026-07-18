"""Analytics marts: wholesale rebuild after each load (fast at this scale).

Medians via percentile_cont; Sales only (mortgages/gifts are price outliers);
per-m2 values clamped to a sane range to keep data-entry glitches out of the
aggregates.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_AREA_MART_SQL = """
INSERT INTO mart_area_monthly (
    area_id, month, usage, sale_cnt, sale_median_price_m2, sale_p25_price_m2,
    sale_p75_price_m2, rent_cnt, rent_median_annual_m2, gross_yield_pct
)
SELECT
    coalesce(s.area_id, r.area_id),
    coalesce(s.month, r.month),
    coalesce(s.usage, r.usage),
    s.cnt,
    s.med, s.p25, s.p75,
    r.cnt,
    r.med,
    round((r.med / nullif(s.med, 0) * 100)::numeric, 2)
FROM (
    SELECT f.area_id,
           date_trunc('month', f.txn_date)::date AS month,
           coalesce(pt.usage, 'Unknown') AS usage,
           count(*)::int AS cnt,
           round((percentile_cont(0.5) WITHIN GROUP (ORDER BY f.price_per_m2))::numeric, 2) AS med,
           round((percentile_cont(0.25) WITHIN GROUP (ORDER BY f.price_per_m2))::numeric, 2) AS p25,
           round((percentile_cont(0.75) WITHIN GROUP (ORDER BY f.price_per_m2))::numeric, 2) AS p75
    FROM fact_sale_transaction f
    LEFT JOIN dim_property_type pt ON pt.id = f.property_type_id
    WHERE f.txn_group = 'Sales' AND f.price_per_m2 BETWEEN 500 AND 200000
    GROUP BY 1, 2, 3
) s
FULL OUTER JOIN (
    SELECT f.area_id,
           date_trunc('month', f.registration_date)::date AS month,
           coalesce(pt.usage, 'Unknown') AS usage,
           count(*)::int AS cnt,
           round((percentile_cont(0.5) WITHIN GROUP (ORDER BY f.rent_per_m2_year))::numeric, 2) AS med
    FROM fact_rent_contract f
    LEFT JOIN dim_property_type pt ON pt.id = f.property_type_id
    WHERE f.rent_per_m2_year BETWEEN 50 AND 20000
    GROUP BY 1, 2, 3
) r USING (area_id, month, usage)
"""

_PROJECT_MART_SQL = """
INSERT INTO mart_project_monthly (
    project_id, month, usage, sale_cnt, sale_median_price_m2, sale_p25_price_m2,
    sale_p75_price_m2, rent_cnt, rent_median_annual_m2, gross_yield_pct
)
SELECT
    coalesce(s.project_id, r.project_id),
    coalesce(s.month, r.month),
    coalesce(s.usage, r.usage),
    s.cnt,
    s.med, s.p25, s.p75,
    r.cnt,
    r.med,
    round((r.med / nullif(s.med, 0) * 100)::numeric, 2)
FROM (
    SELECT f.project_id,
           date_trunc('month', f.txn_date)::date AS month,
           coalesce(pt.usage, 'Unknown') AS usage,
           count(*)::int AS cnt,
           round((percentile_cont(0.5) WITHIN GROUP (ORDER BY f.price_per_m2))::numeric, 2) AS med,
           round((percentile_cont(0.25) WITHIN GROUP (ORDER BY f.price_per_m2))::numeric, 2) AS p25,
           round((percentile_cont(0.75) WITHIN GROUP (ORDER BY f.price_per_m2))::numeric, 2) AS p75
    FROM fact_sale_transaction f
    LEFT JOIN dim_property_type pt ON pt.id = f.property_type_id
    WHERE f.project_id IS NOT NULL
      AND f.txn_group = 'Sales' AND f.price_per_m2 BETWEEN 500 AND 200000
    GROUP BY 1, 2, 3
) s
FULL OUTER JOIN (
    SELECT f.project_id,
           date_trunc('month', f.registration_date)::date AS month,
           coalesce(pt.usage, 'Unknown') AS usage,
           count(*)::int AS cnt,
           round((percentile_cont(0.5) WITHIN GROUP (ORDER BY f.rent_per_m2_year))::numeric, 2) AS med
    FROM fact_rent_contract f
    LEFT JOIN dim_property_type pt ON pt.id = f.property_type_id
    WHERE f.project_id IS NOT NULL AND f.rent_per_m2_year BETWEEN 50 AND 20000
    GROUP BY 1, 2, 3
) r USING (project_id, month, usage)
"""


def rebuild_marts(session: Session) -> dict:
    session.execute(text("TRUNCATE mart_area_monthly"))
    area_rows = session.execute(text(_AREA_MART_SQL)).rowcount
    session.execute(text("TRUNCATE mart_project_monthly"))
    project_rows = session.execute(text(_PROJECT_MART_SQL)).rowcount
    session.commit()
    log.info("marts rebuilt: area=%s project=%s", area_rows, project_rows)
    return {"mart_area_monthly": area_rows, "mart_project_monthly": project_rows}
