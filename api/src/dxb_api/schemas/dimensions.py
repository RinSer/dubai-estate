from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from dxb_api.schemas.common import GeoFlags


class Area(GeoFlags):
    """A DLD community/area — the primary unit of geographic analysis."""

    id: int
    dld_area_code: str | None = Field(None, description="DLD code, e.g. 'A-292'.")
    name_en: str = Field(..., description="Canonical UPPER-cased English name.")
    name_ar: str | None = None
    zone_name: str | None = None
    geo_match_method: str | None = Field(
        None,
        description=(
            "How geometry was matched to this area: exact | parent_fallback | "
            "manual. parent_fallback means the shape belongs to a larger "
            "parent area, so it is approximate."
        ),
    )


class Project(GeoFlags):
    """A development project. Master projects contain sub-projects."""

    id: int
    dld_project_number: int | None = None
    name_en: str
    name_ar: str | None = None
    master_project_id: int | None = Field(
        None, description="Parent project id, when this is a sub-project."
    )
    master_project_en: str | None = None
    is_master: bool = Field(
        ...,
        description=(
            "True for master developments. The same name can exist as both a "
            "master and a regular project, so this disambiguates."
        ),
    )
    area_id: int | None = None
    developer_id: int | None = None
    status: str | None = None
    project_type: str | None = None
    percent_completed: Decimal | None = None
    cnt_units: int | None = None
    completion_date: date | None = None


class Developer(BaseModel):
    id: int
    dld_number: int | None = None
    name_en: str
    name_ar: str | None = None


class PropertyType(BaseModel):
    id: int
    usage: str
    prop_type: str
    prop_subtype: str | None = None


class Usage(BaseModel):
    """A raw `usage` value exactly as it appears in the data.

    Not normalized on purpose: the real values include Arabic text and near
    duplicates, and there is **no 'office' category**. Listing them is how a
    caller discovers the true vocabulary instead of inventing one.
    """

    usage: str
    property_type_count: int


class Source(BaseModel):
    id: int
    code: str
    name: str
    base_url: str
    license: str | None = None
    is_government: bool = Field(
        ...,
        description="True for verified government data; false for third-party mirrors.",
    )
