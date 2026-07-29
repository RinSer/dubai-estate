"""Analytics: server-computed answers to the four question shapes.

These exist so an LLM makes **one** call and narrates a verified number,
instead of paging raw facts and doing the arithmetic itself — which is exactly
where it would hallucinate.

Division of labour: SQL fetches the monthly series (the marts are small —
86k area rows, 207k project rows — so this is cheap), and
`dxb_api.domain.metrics` computes the numbers in pure Python. That split is on
purpose: the arithmetic is the part most likely to be subtly wrong, and in
Python it is directly unit-testable without a database.
"""

from __future__ import annotations

from datetime import date

from dxb_core.models import (
    DimArea,
    DimBuilding,
    DimProject,
    MartAreaMonthly,
    MartBuildingSummary,
    MartProjectMonthly,
)
from sqlalchemy import select

from dxb_api.domain import caveats, metrics
from dxb_api.errors import ValidationError
from dxb_api.repositories.base import BaseRepository

_METRICS = {
    "capital_growth": "capital_growth_cagr_pct",
    "gross_yield": "gross_rental_yield_pct",
    "total_return": "gross_total_return_pct",
    "sale_price_m2": "median_sale_aed_m2",
    "rent_m2": "median_rent_aed_m2_year",
}

# Buildings are sales-only, permanently: no rent contract carries a building
# identifier, so there is nothing to divide a rent by (BUILDING_MART_ANALYSIS.md
# §2). These are the only two metrics that can exist at that grain — asking for
# the others is answered with a clear refusal, not a null.
_BUILDING_METRICS = frozenset({"capital_growth", "sale_price_m2"})

_ENTITY_KINDS = ("area", "project", "building")

# Why a building cannot be compared on income. Stated once, forwarded wherever
# a building appears next to entities that *do* have a yield, so a null is
# never left to be interpreted as "zero" or "we didn't look".
BUILDING_NO_INCOME = (
    "Rental yield and total return do not exist for buildings: no rent "
    "contract in the source data carries a building identifier, so there is "
    "nothing to compute income from. This is a permanent limit of the source, "
    "not missing data that might appear later. Capital growth and price level "
    "are available."
)


class AnalyticsRepository(BaseRepository):
    # ------------------------------------------------------ series fetch

    async def _series(
        self,
        *,
        entity: str,
        ids: list[int] | None,
        usage: str | None,
        month_from: date | None,
        month_to: date | None,
        min_sample: int,
        has_geo_data: bool | None,
        geo_level: str | None,
    ) -> dict[int, dict]:
        """Monthly points per entity, keyed by id, with the entity name."""
        if entity == "area":
            m, dim, id_col = MartAreaMonthly, DimArea, MartAreaMonthly.area_id
        else:
            m, dim, id_col = (
                MartProjectMonthly,
                DimProject,
                MartProjectMonthly.project_id,
            )

        stmt = select(
            id_col.label("entity_id"),
            dim.name_en,
            m.month,
            m.sale_median_price_m2,
            m.sale_cnt,
            m.rent_median_annual_m2,
            m.rent_cnt,
        ).join(dim, dim.id == id_col)

        if entity == "area":
            if geo_level == "polygon":
                stmt = stmt.where(DimArea.boundary.isnot(None))
            elif geo_level == "point":
                stmt = stmt.where(DimArea.centroid.isnot(None))
            elif has_geo_data is True:
                stmt = stmt.where(
                    (DimArea.centroid.isnot(None)) | (DimArea.boundary.isnot(None))
                )
        elif geo_level == "point" or has_geo_data is True:
            stmt = stmt.where(DimProject.location.isnot(None))
        elif geo_level == "polygon":
            stmt = stmt.where(DimProject.id.is_(None))

        if ids:
            stmt = stmt.where(id_col.in_(ids))
        if usage:
            stmt = stmt.where(m.usage == usage)
        if month_from is not None:
            stmt = stmt.where(m.month >= month_from)
        if month_to is not None:
            stmt = stmt.where(m.month <= month_to)
        # Analytics never reach into future-dated months: annualizing a lease
        # that has not started is not a measurement of anything.
        stmt = stmt.where(m.month <= date.today())

        rows = (await self._session.execute(stmt)).all()

        out: dict[int, dict] = {}
        for r in rows:
            bucket = out.setdefault(
                r.entity_id, {"id": r.entity_id, "name_en": r.name_en, "points": []}
            )
            sale_ok = r.sale_cnt is not None and r.sale_cnt >= min_sample
            rent_ok = r.rent_cnt is not None and r.rent_cnt >= min_sample
            bucket["points"].append(
                metrics.MonthPoint(
                    month=r.month,
                    # Below min_sample the median is noise, so the value is
                    # dropped while the month itself is kept -- suppressing
                    # the number, not pretending the month never existed.
                    sale_median_price_m2=(
                        float(r.sale_median_price_m2)
                        if sale_ok and r.sale_median_price_m2 is not None
                        else None
                    ),
                    sale_cnt=int(r.sale_cnt or 0) if sale_ok else 0,
                    rent_median_annual_m2=(
                        float(r.rent_median_annual_m2)
                        if rent_ok and r.rent_median_annual_m2 is not None
                        else None
                    ),
                    rent_cnt=int(r.rent_cnt or 0) if rent_ok else 0,
                )
            )
        return out

    # ------------------------------------------------- building summaries

    async def _building_summaries(
        self,
        *,
        ids: list[int] | None,
        usage: str | None,
        min_sample: int,
    ) -> dict[int, dict]:
        """Building rows, reshaped to look like a `metrics.summarize()` result.

        Buildings have no monthly series to summarize — the mart is one row per
        (building, usage) because a monthly grain at this level would be 42%
        single-sale cells (BUILDING_MART_ANALYSIS.md §3). So the equivalent
        figures are read straight from the mart, which already applied the
        honesty guards when it was built.

        Presenting them under the same key names as the monthly path is what
        lets a building sit next to an area in one comparison. The income keys
        are deliberately absent rather than null-filled: `summarize()` returns
        None for "we looked and could not compute it", and that is a different
        statement from "this cannot exist".
        """
        m = MartBuildingSummary
        stmt = select(
            m.building_id,
            DimBuilding.name_en,
            m.usage,
            m.sale_cnt_12m,
            m.median_price_m2_12m,
            m.sale_cnt_total,
            m.median_price_m2_all,
            m.first_sale,
            m.last_sale,
            m.price_m2_cagr_pct,
            m.cagr_years,
            m.cagr_sample_size,
            m.sample_tier,
        ).join(DimBuilding, DimBuilding.id == m.building_id)

        if ids:
            stmt = stmt.where(m.building_id.in_(ids))
        if usage:
            stmt = stmt.where(m.usage == usage)
        if min_sample:
            stmt = stmt.where(m.sale_cnt_12m >= min_sample)

        rows = (await self._session.execute(stmt)).all()

        out: dict[int, dict] = {}
        for r in rows:
            # One row per (building, usage); without a usage filter keep the
            # best-evidenced one rather than an arbitrary first.
            existing = out.get(r.building_id)
            if existing and (existing["sample_size"] or 0) >= (r.sale_cnt_12m or 0):
                continue
            out[r.building_id] = {
                "id": r.building_id,
                "name_en": r.name_en,
                "usage": r.usage,
                "capital_growth_cagr_pct": (
                    float(r.price_m2_cagr_pct)
                    if r.price_m2_cagr_pct is not None
                    else None
                ),
                "median_sale_aed_m2": (
                    float(r.median_price_m2_12m)
                    if r.median_price_m2_12m is not None
                    else None
                ),
                "median_sale_aed_m2_lifetime": (
                    float(r.median_price_m2_all)
                    if r.median_price_m2_all is not None
                    else None
                ),
                "sample_size": int(r.sale_cnt_12m or 0),
                "sample_size_lifetime": int(r.sale_cnt_total or 0),
                "years": float(r.cagr_years) if r.cagr_years is not None else None,
                "cagr_sample_size": r.cagr_sample_size,
                "sample_tier": r.sample_tier,
                "month_from": r.first_sale,
                "month_to": r.last_sale,
                "income_metrics_unavailable": BUILDING_NO_INCOME,
            }
        return out

    # ---------------------------------------------------------- ranking

    async def ranking(
        self,
        *,
        entity: str = "area",
        metric: str = "total_return",
        month_from: date | None = None,
        month_to: date | None = None,
        usage: str | None = None,
        min_sample: int | None = None,
        has_geo_data: bool | None = None,
        geo_level: str | None = None,
        limit: int = 25,
        ascending: bool = False,
    ) -> dict:
        if metric not in _METRICS:
            raise ValidationError(
                f"Unknown metric {metric!r}", allowed=sorted(_METRICS)
            )
        if entity not in _ENTITY_KINDS:
            raise ValidationError(
                f"Cannot rank {entity!r}", allowed=list(_ENTITY_KINDS)
            )
        min_sample = (
            self._settings.default_min_sample if min_sample is None else min_sample
        )

        if entity == "building":
            return await self._building_ranking(
                metric=metric,
                usage=usage,
                min_sample=min_sample,
                limit=limit,
                ascending=ascending,
            )

        series = await self._series(
            entity=entity,
            ids=None,
            usage=usage,
            month_from=month_from,
            month_to=month_to,
            min_sample=min_sample,
            has_geo_data=has_geo_data,
            geo_level=geo_level,
        )

        key = _METRICS[metric]
        results = []
        for bucket in series.values():
            summary = metrics.summarize(bucket["points"])
            if summary.get(key) is None:
                continue  # not enough data to state this metric honestly
            results.append(
                {"id": bucket["id"], "name_en": bucket["name_en"], **summary}
            )

        results.sort(key=lambda r: r[key], reverse=not ascending)
        excluded = len(series) - len(results)
        return {
            "entity": entity,
            "metric": metric,
            "metric_field": key,
            "items": results[:limit],
            "applied": {
                "min_sample": min_sample,
                "month_from": month_from,
                "month_to": month_to,
                "usage": usage,
                "has_geo_data": has_geo_data,
                "geo_level": geo_level,
                "ascending": ascending,
            },
            "entities_considered": len(series),
            "entities_excluded_insufficient_data": excluded,
            **caveats.block(),
        }

    async def _building_ranking(
        self,
        *,
        metric: str,
        usage: str | None,
        min_sample: int,
        limit: int,
        ascending: bool,
    ) -> dict:
        if metric not in _BUILDING_METRICS:
            raise ValidationError(
                f"{metric!r} cannot be computed for buildings. {BUILDING_NO_INCOME}",
                allowed=sorted(_BUILDING_METRICS),
                entity="building",
            )
        summaries = await self._building_summaries(
            ids=None, usage=usage, min_sample=min_sample
        )
        key = _METRICS[metric]
        results = [b for b in summaries.values() if b.get(key) is not None]
        results.sort(key=lambda r: r[key], reverse=not ascending)

        return {
            "entity": "building",
            "metric": metric,
            "metric_field": key,
            "items": results[:limit],
            "applied": {
                "min_sample": min_sample,
                "usage": usage,
                "ascending": ascending,
            },
            "entities_considered": len(summaries),
            "entities_excluded_insufficient_data": len(summaries) - len(results),
            **caveats.block(
                [
                    BUILDING_NO_INCOME,
                    "Building figures are a trailing-12-month price level and "
                    "a multi-year CAGR, not a monthly series. `sample_tier` "
                    "rates the price level: 'thin' and 'insufficient' should "
                    "be quoted with that caveat attached.",
                ]
            ),
        }

    # ----------------------------------------------------------- growth

    async def growth(
        self,
        *,
        entity: str,
        entity_id: int,
        month_from: date | None = None,
        month_to: date | None = None,
        usage: str | None = None,
        min_sample: int | None = None,
        resolved: dict | None = None,
    ) -> dict:
        min_sample = (
            self._settings.default_min_sample if min_sample is None else min_sample
        )
        series = await self._series(
            entity=entity,
            ids=[entity_id],
            usage=usage,
            month_from=month_from,
            month_to=month_to,
            min_sample=min_sample,
            has_geo_data=None,
            geo_level=None,
        )
        bucket = series.get(entity_id)
        if bucket is None:
            return {
                "entity": entity,
                "id": entity_id,
                "name_en": None,
                "series": [],
                "yoy": [],
                "consecutive_yoy_increases": 0,
                "resolved_entity": resolved,
                "applied": {"min_sample": min_sample, "usage": usage},
                **caveats.block(["No mart rows matched these filters."]),
            }

        points = bucket["points"]
        steps = metrics.yoy_steps(points)
        summary = metrics.summarize(points)
        return {
            "entity": entity,
            "id": bucket["id"],
            "name_en": bucket["name_en"],
            **summary,
            "series": [
                {
                    "month": p.month,
                    "sale_median_price_m2": p.sale_median_price_m2,
                    "sale_cnt": p.sale_cnt,
                    "rent_median_annual_m2": p.rent_median_annual_m2,
                    "rent_cnt": p.rent_cnt,
                }
                for p in sorted(points, key=lambda p: p.month)
            ],
            "yoy": steps,
            "consecutive_yoy_increases": metrics.consecutive_yoy_increases(steps),
            "resolved_entity": resolved,
            "applied": {"min_sample": min_sample, "usage": usage},
            **caveats.block(),
        }

    # ------------------------------------------------------------ yield

    async def yields(
        self,
        *,
        entity: str = "area",
        month_from: date | None = None,
        month_to: date | None = None,
        usage: str | None = None,
        min_sample: int | None = None,
        has_geo_data: bool | None = None,
        geo_level: str | None = None,
        limit: int = 25,
        ascending: bool = False,
    ) -> dict:
        return await self.ranking(
            entity=entity,
            metric="gross_yield",
            month_from=month_from,
            month_to=month_to,
            usage=usage,
            min_sample=min_sample,
            has_geo_data=has_geo_data,
            geo_level=geo_level,
            limit=limit,
            ascending=ascending,
        )

    # ---------------------------------------------------------- compare

    async def compare(
        self,
        *,
        dimension: str,
        values: list[str],
        month_from: date | None = None,
        month_to: date | None = None,
        min_sample: int | None = None,
    ) -> dict:
        """Side-by-side metrics across usages, across named entities, or across
        entities of *different* types.

        `dimension=usage` is what answers "office vs residential" — note the
        data has no "office" usage at all, so the caller must first look at
        /dimensions/usages rather than assume the category exists.

        `dimension=mixed` is the cross-type case, and it is the most useful one:
        values are `type:value` specs, so a project can be compared against the
        area it sits in ("is this development beating its neighbourhood?").
        Restricting comparison to a single type would have made that question —
        the one an investor actually asks — impossible to express.
        """
        min_sample = (
            self._settings.default_min_sample if min_sample is None else min_sample
        )
        allowed_dimensions = ["usage", "area", "project", "mixed"]
        if dimension not in allowed_dimensions:
            raise ValidationError(
                f"Unknown dimension {dimension!r}", allowed=allowed_dimensions
            )
        if not values:
            raise ValidationError(
                "compare requires at least one value", dimension=dimension
            )
        if len(values) > self._settings.max_entity_ids:
            raise ValidationError(
                f"compare accepts at most {self._settings.max_entity_ids} values",
                requested=len(values),
            )

        groups = []
        if dimension == "usage":
            for value in values:
                series = await self._series(
                    entity="area",
                    ids=None,
                    usage=value,
                    month_from=month_from,
                    month_to=month_to,
                    min_sample=min_sample,
                    has_geo_data=None,
                    geo_level=None,
                )
                # Pool every area's months for this usage into one series.
                pooled = [p for b in series.values() for p in b["points"]]
                groups.append(
                    {
                        "value": value,
                        "entities_pooled": len(series),
                        **metrics.summarize(pooled),
                    }
                )
        else:
            for value in values:
                kind, spec = self._split_spec(dimension, value)
                groups.append(
                    await self._entity_group(
                        kind=kind,
                        spec=spec,
                        label=value,
                        month_from=month_from,
                        month_to=month_to,
                        min_sample=min_sample,
                    )
                )

        extra = []
        kinds = {g.get("entity_type") for g in groups if g.get("entity_type")}
        if "building" in kinds:
            extra.append(BUILDING_NO_INCOME)
        if len(kinds) > 1:
            # The comparison is legitimate but the grains differ, and saying so
            # is the difference between an informed reading and a false
            # equivalence. An area pools thousands of transactions across many
            # buildings; a single project or building does not.
            extra.append(
                "This compares entities of different types "
                f"({', '.join(sorted(kinds))}). They are measured the same way "
                "but describe different scopes: an area aggregates every "
                "property within it, so it is a benchmark rather than a peer. "
                "Comparing a project to its own area is a like-for-market "
                "comparison, not like-for-like."
            )

        return {
            "dimension": dimension,
            "groups": groups,
            "applied": {
                "min_sample": min_sample,
                "month_from": month_from,
                "month_to": month_to,
            },
            **caveats.block(extra),
        }

    def _split_spec(self, dimension: str, value: str) -> tuple[str, str]:
        """`mixed` values carry their own type; single-dimension ones inherit it."""
        if dimension != "mixed":
            return dimension, value
        kind, sep, rest = value.partition(":")
        if not sep or kind not in _ENTITY_KINDS:
            raise ValidationError(
                f"{value!r} is not a valid mixed-comparison value. Use "
                f"'type:name' or 'type:id', e.g. 'project:412' or "
                f"'area:Dubai Marina'.",
                allowed_types=list(_ENTITY_KINDS),
            )
        if not rest.strip():
            raise ValidationError(f"{value!r} is missing a name or id after the colon")
        return kind, rest.strip()

    async def _entity_group(
        self,
        *,
        kind: str,
        spec: str,
        label: str,
        month_from,
        month_to,
        min_sample: int,
    ) -> dict:
        """One comparison group, whichever kind of entity it is.

        Buildings take the summary path and the other two take the monthly
        path, but both emit the same metric keys — which is what makes a
        cross-type comparison readable rather than two shapes stapled together.
        """
        if kind == "building":
            entity_id, resolved = await self._resolve_named("building", spec)
            summaries = await self._building_summaries(
                ids=[entity_id], usage=None, min_sample=0
            )
            summary = summaries.get(entity_id)
            if summary is None:
                return {
                    "value": label,
                    "entity_type": "building",
                    "id": entity_id,
                    "name_en": None,
                    "resolved_entity": resolved,
                    "note": "No sales recorded for this building.",
                }
            # min_sample governs whether the price level is trustworthy enough
            # to state; the CAGR carries its own guard from the mart build.
            if min_sample and summary["sample_size"] < min_sample:
                summary = {
                    **summary,
                    "median_sale_aed_m2": None,
                    "note": (
                        f"Only {summary['sample_size']} sales in the trailing "
                        f"12 months, below the requested min_sample of "
                        f"{min_sample}; the price level is withheld rather "
                        f"than quoted as reliable."
                    ),
                }
            return {
                "value": label,
                "entity_type": "building",
                "resolved_entity": resolved,
                **summary,
            }

        entity_id, resolved = await self._resolve_named(kind, spec)
        series = await self._series(
            entity=kind,
            ids=[entity_id],
            usage=None,
            month_from=month_from,
            month_to=month_to,
            min_sample=min_sample,
            has_geo_data=None,
            geo_level=None,
        )
        bucket = series.get(entity_id, {"name_en": None, "points": []})
        return {
            "value": label,
            "entity_type": kind,
            "id": entity_id,
            "name_en": bucket.get("name_en"),
            "resolved_entity": resolved,
            **metrics.summarize(bucket["points"]),
        }

    async def _resolve_named(
        self, dimension: str, value: str
    ) -> tuple[int, dict | None]:
        if value.isdigit():
            return int(value), None
        from dxb_api.repositories.dimensions import DimensionRepository

        repo = DimensionRepository(self._session, self._settings)
        return await repo.resolve(dimension, value)
