"""
FastAPI application exposing the generic ingestion service:

  GET  /sources                 -- list configured sources (loaded from YAML)
  POST /sources/{name}/ingest   -- trigger an ingestion run for one source
  GET  /runs/{run_id}           -- observe the status/metrics of a run
  GET  /runs/{run_id}/records   -- sample of records ingested by a run
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import SourceConfig, load_all_source_configs
from app.db import get_session, init_db
from app.models import IngestionRun, RawRecord
from app.orchestrator import IngestionOrchestrator

logging.basicConfig(level=logging.INFO)

SOURCE_CONFIGS: dict[str, SourceConfig] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    SOURCE_CONFIGS.update(load_all_source_configs())
    yield


app = FastAPI(
    title="Generic Data Ingestion Service",
    description="Config-driven ingestion of arbitrary external APIs into a database.",
    lifespan=lifespan,
)

orchestrator = IngestionOrchestrator()


@app.get("/sources")
async def list_sources():
    return {
        name: {
            "base_url": cfg.base_url,
            "auth_type": cfg.auth.type.value,
            "pagination_type": cfg.pagination.type.value,
        }
        for name, cfg in SOURCE_CONFIGS.items()
    }


@app.post("/sources/{name}/ingest")
async def trigger_ingest(name: str, session: AsyncSession = Depends(get_session)):
    config = SOURCE_CONFIGS.get(name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Unknown source '{name}'")

    run = await orchestrator.run(config, session)
    return {
        "run_id": run.id,
        "status": run.status,
        "pages_fetched": run.pages_fetched,
        "records_fetched": run.records_fetched,
        "records_saved": run.records_saved,
        "records_skipped": run.records_skipped,
        "error": run.error,
    }


@app.get("/runs/{run_id}")
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)):
    run = await session.get(IngestionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "id": run.id,
        "source_name": run.source_name,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "pages_fetched": run.pages_fetched,
        "records_fetched": run.records_fetched,
        "records_saved": run.records_saved,
        "records_skipped": run.records_skipped,
        "last_checkpoint": run.last_checkpoint,
        "error": run.error,
    }


@app.get("/runs/{run_id}/records")
async def get_run_records(run_id: int, limit: int = 20, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(RawRecord).where(RawRecord.run_id == run_id).limit(limit)
    )
    records = result.scalars().all()
    return [
        {
            "external_id": r.external_id,
            "source_name": r.source_name,
            "payload": r.payload,
            "ingested_at": r.ingested_at,
        }
        for r in records
    ]


@app.get("/health")
async def health():
    return {"status": "ok"}
