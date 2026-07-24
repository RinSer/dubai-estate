"""GeoJSON for the OSM map.

Geometry is serialized by PostGIS (`ST_AsGeoJSON`) rather than in Python: it
is the authority on the geometry types already stored, and it avoids pulling a
geometry library into the API image for a job the database does natively.

Metrics ride along as feature properties so a choropleth needs exactly one
request, not one per polygon.
"""

from __future__ import annotations

import json
from datetime import date

from dxb_core.models import DimArea, DimProject, MartAreaMonthly
from sqlalchemy import func, literal, select, text

from dxb_api.repositories.base import BaseRepository


class GeoRepository(BaseRepository):
    async def areas_geojson(
        self,
        *,
        geo_level: str = "polygon",
        usage: str | None = None,
        month_from: date | None = None,
        month_to: date | None = None,
        min_sample: int | None = None,
        area_ids: list[int] | None = None,
    ) -> dict:
        area_ids = self._check_id_set(area_ids, "area_ids")
        min_sample = (
            self._settings.default_min_sample if min_sample is None else min_sample
        )

        # Geometry: boundary when asked for polygons, else centroid, with a
        # centroid fallback so an area with only a point still renders.
        if geo_level == "point":
            geom = DimArea.centroid
        else:
            geom = func.coalesce(DimArea.boundary, DimArea.centroid)

        stmt = select(
            DimArea.id,
            DimArea.name_en,
            DimArea.dld_area_code,
            DimArea.zone_name,
            DimArea.boundary.isnot(None).label("has_boundary"),
            func.ST_AsGeoJSON(geom).label("geometry"),
        ).where(geom.isnot(None))
        if area_ids:
            stmt = stmt.where(DimArea.id.in_(area_ids))

        rows = (await self._session.execute(stmt)).all()

        latest = await self._latest_area_metrics(
            usage, month_from, month_to, min_sample
        )

        features = []
        for r in rows:
            props = {
                "area_id": r.id,
                "name_en": r.name_en,
                "dld_area_code": r.dld_area_code,
                "zone_name": r.zone_name,
                "has_boundary": bool(r.has_boundary),
                **latest.get(r.id, {}),
            }
            features.append(
                {
                    "type": "Feature",
                    "id": r.id,
                    "geometry": json.loads(r.geometry),
                    "properties": props,
                }
            )
        return {
            "type": "FeatureCollection",
            "features": features,
            "applied": {
                "geo_level": geo_level,
                "usage": usage,
                "min_sample": min_sample,
                "month_from": month_from,
                "month_to": month_to,
            },
        }

    async def _latest_area_metrics(
        self,
        usage: str | None,
        month_from: date | None,
        month_to: date | None,
        min_sample: int,
    ) -> dict[int, dict]:
        """Most recent qualifying mart month per area, for map styling."""
        m = MartAreaMonthly
        stmt = select(
            m.area_id,
            m.month,
            m.usage,
            m.sale_median_price_m2,
            m.sale_cnt,
            m.rent_median_annual_m2,
            m.rent_cnt,
            m.gross_yield_pct,
        ).where(m.month <= date.today(), m.sale_cnt >= min_sample)
        if usage:
            stmt = stmt.where(m.usage == usage)
        if month_from is not None:
            stmt = stmt.where(m.month >= month_from)
        if month_to is not None:
            stmt = stmt.where(m.month <= month_to)
        stmt = stmt.order_by(m.area_id.asc(), m.month.desc())

        out: dict[int, dict] = {}
        for r in (await self._session.execute(stmt)).all():
            if r.area_id in out:
                continue  # ordered by month desc, so the first is the latest
            out[r.area_id] = {
                "metric_month": r.month.isoformat(),
                "usage": r.usage,
                "sale_median_price_m2": float(r.sale_median_price_m2)
                if r.sale_median_price_m2
                else None,
                "sale_cnt": r.sale_cnt,
                "rent_median_annual_m2": float(r.rent_median_annual_m2)
                if r.rent_median_annual_m2
                else None,
                "rent_cnt": r.rent_cnt,
                "gross_yield_pct": float(r.gross_yield_pct)
                if r.gross_yield_pct
                else None,
            }
        return out

    async def projects_geojson(self, *, project_ids: list[int] | None = None) -> dict:
        """Project points.

        Returns an empty-but-valid FeatureCollection today: every
        `dim_project.location` is NULL until the geolocation enrichment lands.
        Documented in API_DESIGN.md §7 rather than left as a surprise.
        """
        project_ids = self._check_id_set(project_ids, "project_ids")
        stmt = select(
            DimProject.id,
            DimProject.name_en,
            DimProject.area_id,
            DimProject.status,
            func.ST_AsGeoJSON(DimProject.location).label("geometry"),
        ).where(DimProject.location.isnot(None))
        if project_ids:
            stmt = stmt.where(DimProject.id.in_(project_ids))

        rows = (await self._session.execute(stmt)).all()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": r.id,
                    "geometry": json.loads(r.geometry),
                    "properties": {
                        "project_id": r.id,
                        "name_en": r.name_en,
                        "area_id": r.area_id,
                        "status": r.status,
                    },
                }
                for r in rows
            ],
        }
