from __future__ import annotations

import logging
from datetime import date, datetime

import typer
from sqlalchemy import func, select, text

from dxb.config import get_settings

app = typer.Typer(help="Dubai real estate ELT: DLD open data -> Postgres star schema")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _parse(day: str) -> date:
    return datetime.strptime(day, "%Y-%m-%d").date()


@app.command()
def init() -> None:
    """Run migrations and seed dim_source."""
    from dxb.db.engine import init_db

    init_db()
    typer.echo("database ready")


@app.command()
def collect(
    endpoint: str = typer.Argument(help="areas | projects | transactions | rents"),
    from_date: str = typer.Option(None, "--from", help="YYYY-MM-DD"),
    to_date: str = typer.Option(None, "--to", help="YYYY-MM-DD"),
) -> None:
    """Collect one endpoint into staging."""
    from dxb.collectors.client import DldClient
    from dxb.collectors.dld import (
        collect_areas,
        collect_projects,
        collect_windowed,
        default_window,
    )
    from dxb.db.engine import get_session, source_id

    settings = get_settings()
    with get_session() as session, DldClient(
        page_size=settings.page_size,
        throttle_seconds=settings.throttle_seconds,
        max_concurrency=settings.max_concurrency,
    ) as client:
        sid = source_id(session)
        if endpoint == "areas":
            report = collect_areas(session, client, sid)
        elif endpoint == "projects":
            report = collect_projects(session, client, sid)
        elif endpoint in ("transactions", "rents"):
            if from_date and to_date:
                d_from, d_to = _parse(from_date), _parse(to_date)
            else:
                d_from, d_to = default_window(session, sid, endpoint)
            report = collect_windowed(session, client, sid, endpoint, d_from, d_to)
        else:
            raise typer.BadParameter(f"unknown endpoint {endpoint!r}")
    typer.echo(report)


@app.command()
def transform() -> None:
    """Transform staged rows into dims and facts."""
    from dxb.db.engine import get_session
    from dxb.transform.dld import transform_all

    with get_session() as session:
        report = transform_all(session, get_settings().source_url)
    typer.echo(report)


@app.command()
def marts() -> None:
    """Rebuild analytics marts."""
    from dxb.db.engine import get_session
    from dxb.marts import rebuild_marts

    with get_session() as session:
        typer.echo(rebuild_marts(session))


@app.command()
def backfill(
    from_date: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    to_date: str = typer.Option(None, "--to", help="YYYY-MM-DD (default today)"),
) -> None:
    """Full pipeline over an explicit historical range (resumable)."""
    from dxb.pipeline import run_with_retries

    d_to = _parse(to_date) if to_date else date.today()
    report = run_with_retries(kind="backfill", date_from=_parse(from_date), date_to=d_to)
    raise typer.Exit(0 if report else 1)


@app.command("run-once")
def run_once() -> None:
    """One daily pipeline run now (with retries + alerting)."""
    from dxb.pipeline import run_with_retries

    report = run_with_retries(kind="manual")
    raise typer.Exit(0 if report else 1)


@app.command("run-scheduler")
def run_scheduler() -> None:
    """Long-lived scheduler (container default command)."""
    from dxb.scheduler import main

    main()


@app.command()
def stats() -> None:
    """Row counts and a quick market sanity report."""
    from dxb.db.engine import get_session
    from dxb.db.models import (
        DimArea,
        DimProject,
        FactRentContract,
        FactSaleTransaction,
        StgRaw,
    )

    with get_session() as session:
        for label, model in [
            ("stg_raw", StgRaw),
            ("dim_area", DimArea),
            ("dim_project", DimProject),
            ("fact_sale_transaction", FactSaleTransaction),
            ("fact_rent_contract", FactRentContract),
        ]:
            typer.echo(f"{label:26} {session.scalar(select(func.count()).select_from(model)):>10,}")
        unprocessed = session.scalar(
            select(func.count()).select_from(StgRaw).where(StgRaw.processed_at.is_(None))
        )
        typer.echo(f"{'stg_raw unprocessed':26} {unprocessed:>10,}")

        typer.echo("\nTop-10 areas by median sale AED/m2 (residential, latest full month):")
        rows = session.execute(text("""
            WITH latest AS (
                SELECT max(month) AS month FROM mart_area_monthly
                WHERE usage = 'Residential' AND sale_cnt >= 10
            )
            SELECT a.name_en, m.sale_cnt, m.sale_median_price_m2,
                   m.rent_median_annual_m2, m.gross_yield_pct
            FROM mart_area_monthly m
            JOIN dim_area a ON a.id = m.area_id, latest
            WHERE m.month = latest.month AND m.usage = 'Residential' AND m.sale_cnt >= 10
            ORDER BY m.sale_median_price_m2 DESC NULLS LAST
            LIMIT 10
        """)).all()
        for name, cnt, med, rent_med, yield_pct in rows:
            typer.echo(
                f"  {name:35} sales={cnt:>5} median={med or 0:>10,.0f} "
                f"rent/m2/yr={rent_med or 0:>7,.0f} yield={yield_pct or 0:>5}%"
            )


if __name__ == "__main__":
    app()
