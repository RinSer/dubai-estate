from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from dxb_api.deps import GeoRepoDep, PrincipalDep, csv_ints

router = APIRouter(prefix="/geo", tags=["geo"])


@router.get(
    "/areas",
    summary="Areas as GeoJSON, with metrics as feature properties",
    description=(
        "One request renders a choropleth: geometry and the latest qualifying "
        "metrics arrive together, so there is no per-polygon follow-up call. "
        "The geo filter is implicit — areas without geometry are absent."
    ),
)
async def areas_geojson(
    repo: GeoRepoDep,
    _: PrincipalDep,
    geo_level: Annotated[
        Literal["point", "polygon"],
        Query(
            description="polygon falls back to the centroid when no boundary exists."
        ),
    ] = "polygon",
    usage: str | None = None,
    month_from: date | None = None,
    month_to: date | None = None,
    min_sample: int | None = None,
    area_ids: Annotated[str | None, Query(description="Comma-separated ids.")] = None,
):
    return await repo.areas_geojson(
        geo_level=geo_level,
        usage=usage,
        month_from=month_from,
        month_to=month_to,
        min_sample=min_sample,
        area_ids=csv_ints(area_ids),
    )


@router.get(
    "/projects",
    summary="Projects as GeoJSON points",
    description=(
        "Returns an empty-but-valid FeatureCollection until project "
        "geolocation enrichment lands: every dim_project.location is "
        "currently NULL. Documented, not a surprise."
    ),
)
async def projects_geojson(
    repo: GeoRepoDep,
    _: PrincipalDep,
    project_ids: Annotated[
        str | None, Query(description="Comma-separated ids.")
    ] = None,
):
    return await repo.projects_geojson(project_ids=csv_ints(project_ids))
