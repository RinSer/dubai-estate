"""Analytics marts: wholesale rebuild after each load (fast at this scale).

Medians via percentile_cont; Sales only (mortgages/gifts are price outliers);
per-m2 values clamped to a sane range to keep data-entry glitches out of the
aggregates.

Source precedence (docs/DATADUBAI_REBUILD_PLAN.md §3): where two sources
overlap in time, the aggregation — not the fact table — picks the winner:

    data.dubai row  -> always kept (an export is a complete snapshot)
    gateway row     -> kept only past the cutover, where it stops duplicating

Raw facts keep everything from both sources; only the mart deduplicates. This
is why no cross-source record matching is needed (it is impossible for
mortgages/gifts/rents) and why the gateway's 2-day overlap is harmless.
Rebuilding wholesale means moving a cutover re-segregates everything.

The gateway side is compared on the column expressing *its* boundary —
txn_date for sales, registration_date for rents — because a lease is
registered long before it starts. The reporting month is a separate axis:
txn_date for sales, start_date for rents (both populated by both sources).
"""

from __future__ import annotations

import logging

from dxb_core.models import EtlSourceCutover
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def _precedence(
    session: Session, dataset: str, alias: str, gateway_axis: str, tag: str
):
    """SQL fragment + params applying source precedence for one dataset.

    data.dubai rows are always kept — an export is a complete authoritative
    snapshot as of its export date. Other sources (the gateway) are kept only
    past the cutover, which is where their rows stop being duplicates.

    `gateway_axis` is the column that expresses *that source's* boundary:
      transactions -> txn_date          (transaction date ~ registration date)
      rents        -> registration_date (start_date would be the wrong axis:
                      leases start long after they are registered)

    No cutover configured (data.dubai never loaded) -> no filtering.
    """
    row = session.get(EtlSourceCutover, dataset)
    if row is None:
        return "", {}
    clause = (
        f" AND ({alias}.source_id = :src_{tag}"
        f" OR {alias}.{gateway_axis}::date > :cut_{tag})"
    )
    return clause, {f"src_{tag}": row.source_id, f"cut_{tag}": row.cutover_date}


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
           round((percentile_cont(0.5)
                  WITHIN GROUP (ORDER BY f.price_per_m2))::numeric, 2) AS med,
           round((percentile_cont(0.25)
                  WITHIN GROUP (ORDER BY f.price_per_m2))::numeric, 2) AS p25,
           round((percentile_cont(0.75)
                  WITHIN GROUP (ORDER BY f.price_per_m2))::numeric, 2) AS p75
    FROM fact_sale_transaction f
    LEFT JOIN dim_property_type pt ON pt.id = f.property_type_id
    WHERE f.txn_group = 'Sales' AND f.price_per_m2 BETWEEN 500 AND 200000
      AND f.txn_date >= DATE '1990-01-01'{sale_prec}
    GROUP BY 1, 2, 3
) s
FULL OUTER JOIN (
    SELECT f.area_id,
           date_trunc('month', f.start_date)::date AS month,
           coalesce(pt.usage, 'Unknown') AS usage,
           count(*)::int AS cnt,
           round((percentile_cont(0.5)
                  WITHIN GROUP (ORDER BY f.rent_per_m2_year))::numeric, 2) AS med
    FROM fact_rent_contract f
    LEFT JOIN dim_property_type pt ON pt.id = f.property_type_id
    WHERE f.rent_per_m2_year BETWEEN 50 AND 20000
      AND f.start_date IS NOT NULL
      AND f.start_date <= CURRENT_DATE + INTERVAL '2 years'{rent_prec}
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
           round((percentile_cont(0.5)
                  WITHIN GROUP (ORDER BY f.price_per_m2))::numeric, 2) AS med,
           round((percentile_cont(0.25)
                  WITHIN GROUP (ORDER BY f.price_per_m2))::numeric, 2) AS p25,
           round((percentile_cont(0.75)
                  WITHIN GROUP (ORDER BY f.price_per_m2))::numeric, 2) AS p75
    FROM fact_sale_transaction f
    LEFT JOIN dim_property_type pt ON pt.id = f.property_type_id
    WHERE f.project_id IS NOT NULL
      AND f.txn_group = 'Sales' AND f.price_per_m2 BETWEEN 500 AND 200000
      AND f.txn_date >= DATE '1990-01-01'{sale_prec}
    GROUP BY 1, 2, 3
) s
FULL OUTER JOIN (
    SELECT f.project_id,
           date_trunc('month', f.start_date)::date AS month,
           coalesce(pt.usage, 'Unknown') AS usage,
           count(*)::int AS cnt,
           round((percentile_cont(0.5)
                  WITHIN GROUP (ORDER BY f.rent_per_m2_year))::numeric, 2) AS med
    FROM fact_rent_contract f
    LEFT JOIN dim_property_type pt ON pt.id = f.property_type_id
    WHERE f.project_id IS NOT NULL AND f.rent_per_m2_year BETWEEN 50 AND 20000
      AND f.start_date IS NOT NULL
      AND f.start_date <= CURRENT_DATE + INTERVAL '2 years'{rent_prec}
    GROUP BY 1, 2, 3
) r USING (project_id, month, usage)
"""


# --------------------------------------------------------- building summary

# Guards on the coarse CAGR. A building-level trend is only honest when the
# span is long enough for annualization to mean anything AND both ends carry
# more than a couple of sales — see docs/BUILDING_MART_ANALYSIS.md §3.
_CAGR_MIN_YEARS = 2.0
_CAGR_ANCHOR_MIN_N = 5
# Reliability of the trailing-12-month price level, reported as sample_tier.
_TIER_STRONG_N = 20
_TIER_THIN_N = 5

# One row per (building, usage). NOT monthly on purpose: at building level a
# monthly grain is 42% single-sale cells (analysis doc §3). Anchors mirror the
# API's metrics.py approach — average over a window at each end, span measured
# between the windows' midpoints — but at a coarser, sample-safe granularity.
_BUILDING_MART_SQL = """
INSERT INTO mart_building_summary (
    building_id, usage,
    sale_cnt_12m, median_price_m2_12m, p25_price_m2_12m, p75_price_m2_12m,
    sale_cnt_total, median_price_m2_all, first_sale, last_sale,
    price_m2_cagr_pct, cagr_years, cagr_sample_size, sample_tier
)
WITH base AS (
    SELECT f.building_id AS bid,
           coalesce(pt.usage, 'Unknown') AS usage,
           f.txn_date::date AS d,
           f.price_per_m2 AS ppm
    FROM fact_sale_transaction f
    LEFT JOIN dim_property_type pt ON pt.id = f.property_type_id
    WHERE f.building_id IS NOT NULL
      AND f.txn_group = 'Sales'
      AND f.price_per_m2 BETWEEN 500 AND 200000
      AND f.txn_date >= DATE '1990-01-01'
      AND f.txn_date <= CURRENT_DATE{sale_prec}
),
bounds AS (
    SELECT bid, usage,
           count(*)::int AS cnt_total,
           min(d) AS first_sale,
           max(d) AS last_sale,
           round((percentile_cont(0.5)
                  WITHIN GROUP (ORDER BY ppm))::numeric, 2) AS med_all
    FROM base GROUP BY 1, 2
),
recent AS (
    SELECT bid, usage,
           count(*)::int AS cnt,
           round((percentile_cont(0.5)
                  WITHIN GROUP (ORDER BY ppm))::numeric, 2) AS med,
           round((percentile_cont(0.25)
                  WITHIN GROUP (ORDER BY ppm))::numeric, 2) AS p25,
           round((percentile_cont(0.75)
                  WITHIN GROUP (ORDER BY ppm))::numeric, 2) AS p75
    FROM base
    WHERE d > CURRENT_DATE - INTERVAL '12 months'
    GROUP BY 1, 2
),
early AS (
    SELECT b.bid, b.usage, count(*)::int AS cnt,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY b.ppm) AS med,
           (DATE '1990-01-01' + avg(b.d - DATE '1990-01-01')::int) AS mid
    FROM base b
    JOIN bounds bo ON bo.bid = b.bid AND bo.usage = b.usage
    WHERE b.d < bo.first_sale + INTERVAL '12 months'
    GROUP BY 1, 2
),
late AS (
    SELECT b.bid, b.usage, count(*)::int AS cnt,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY b.ppm) AS med,
           (DATE '1990-01-01' + avg(b.d - DATE '1990-01-01')::int) AS mid
    FROM base b
    JOIN bounds bo ON bo.bid = b.bid AND bo.usage = b.usage
    WHERE b.d > bo.last_sale - INTERVAL '12 months'
    GROUP BY 1, 2
),
cagr AS (
    SELECT e.bid, e.usage,
           (l.mid - e.mid) / 365.25 AS years,
           e.cnt + l.cnt AS anchor_n,
           CASE WHEN (l.mid - e.mid) / 365.25 >= :cagr_min_years
                     AND e.cnt >= :cagr_anchor_n
                     AND l.cnt >= :cagr_anchor_n
                     AND e.med > 0 AND l.med > 0
                THEN (power(l.med / e.med,
                            1.0 / ((l.mid - e.mid) / 365.25)) - 1) * 100
           END AS cagr_pct
    FROM early e
    JOIN late l ON l.bid = e.bid AND l.usage = e.usage
)
SELECT bo.bid, bo.usage,
       r.cnt, r.med, r.p25, r.p75,
       bo.cnt_total, bo.med_all, bo.first_sale, bo.last_sale,
       round(c.cagr_pct::numeric, 2),
       CASE WHEN c.cagr_pct IS NOT NULL THEN round(c.years::numeric, 2) END,
       CASE WHEN c.cagr_pct IS NOT NULL THEN c.anchor_n END,
       CASE WHEN coalesce(r.cnt, 0) >= :tier_strong THEN 'strong'
            WHEN coalesce(r.cnt, 0) >= :tier_thin THEN 'thin'
            ELSE 'insufficient' END
FROM bounds bo
LEFT JOIN recent r ON r.bid = bo.bid AND r.usage = bo.usage
LEFT JOIN cagr c ON c.bid = bo.bid AND c.usage = bo.usage
"""


def rebuild_marts(session: Session) -> dict:
    sale_prec, sale_params = _precedence(
        session, "transactions", "f", "txn_date", "sale"
    )
    rent_prec, rent_params = _precedence(
        session, "rents", "f", "registration_date", "rent"
    )
    params = {**sale_params, **rent_params}

    session.execute(text("TRUNCATE mart_area_monthly"))
    area_rows = session.execute(
        text(_AREA_MART_SQL.format(sale_prec=sale_prec, rent_prec=rent_prec)), params
    ).rowcount
    session.execute(text("TRUNCATE mart_project_monthly"))
    project_rows = session.execute(
        text(_PROJECT_MART_SQL.format(sale_prec=sale_prec, rent_prec=rent_prec)), params
    ).rowcount

    # Buildings: summary grain, sales only. The rent precedence params are not
    # passed because rents cannot reach a building at all (0 of 10.3M rows carry
    # building_id) — see docs/BUILDING_MART_ANALYSIS.md §2.
    session.execute(text("TRUNCATE mart_building_summary"))
    building_rows = session.execute(
        text(_BUILDING_MART_SQL.format(sale_prec=sale_prec)),
        {
            **sale_params,
            "cagr_min_years": _CAGR_MIN_YEARS,
            "cagr_anchor_n": _CAGR_ANCHOR_MIN_N,
            "tier_strong": _TIER_STRONG_N,
            "tier_thin": _TIER_THIN_N,
        },
    ).rowcount

    session.commit()
    log.info(
        "marts rebuilt: area=%s project=%s building=%s "
        "(cutovers applied: sales=%s rents=%s)",
        area_rows,
        project_rows,
        building_rows,
        bool(sale_prec),
        bool(rent_prec),
    )
    return {
        "mart_area_monthly": area_rows,
        "mart_project_monthly": project_rows,
        "mart_building_summary": building_rows,
        "cutover_applied": {"sales": bool(sale_prec), "rents": bool(rent_prec)},
    }
