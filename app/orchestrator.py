"""
IngestionOrchestrator: the only piece that knows the whole end-to-end flow.
For a given SourceConfig it wires together auth + pagination + extraction +
fetching + destination, loops through pages, checkpoints progress into
IngestionRun, and tolerates per-record/per-page failures without aborting the
whole run where possible.
"""
from __future__ import annotations

import datetime as dt
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import build_auth_strategy
from app.config import SourceConfig
from app.destinations import build_destination
from app.extractor import ResponseExtractor
from app.fetcher import HttpFetcher
from app.models import IngestionRun
from app.pagination import build_pagination_strategy

logger = logging.getLogger("ingestion.orchestrator")


class IngestionOrchestrator:
    def __init__(self, fetcher: HttpFetcher | None = None):
        self.fetcher = fetcher or HttpFetcher()

    async def run(self, config: SourceConfig, session: AsyncSession) -> IngestionRun:
        run = IngestionRun(source_name=config.name, status="running")
        session.add(run)
        await session.commit()
        await session.refresh(run)

        auth_strategy = build_auth_strategy(config.auth)
        pagination_strategy = build_pagination_strategy(config.pagination)
        extractor = ResponseExtractor(config.response)
        destination = build_destination(config.destination.type.value, session)

        base_request = self._build_base_request(config)
        current_request = pagination_strategy.initial_request(base_request)

        page_index = 0
        had_error = False

        try:
            while current_request is not None:
                authed_request = auth_strategy.apply(current_request)

                try:
                    response = await self.fetcher.fetch(authed_request)
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    logger.error(
                        "Non-retryable HTTP error on page %d for source '%s': %s",
                        page_index, config.name, exc,
                    )
                    had_error = True
                    run.error = str(exc)
                    break
                except Exception as exc:  # noqa: BLE001 - want to record any failure and stop cleanly
                    logger.error(
                        "Fetch failed on page %d for source '%s': %s", page_index, config.name, exc
                    )
                    had_error = True
                    run.error = str(exc)
                    break

                response_json = None
                try:
                    response_json = response.json()
                except ValueError:
                    logger.warning(
                        "Page %d for source '%s' was not valid JSON; skipping page",
                        page_index, config.name,
                    )

                records = []
                if response_json is not None:
                    try:
                        records = extractor.extract_records(response_json)
                    except ValueError as exc:
                        logger.error(
                            "Failed to extract records on page %d for source '%s': %s",
                            page_index, config.name, exc,
                        )
                        had_error = True
                        run.error = str(exc)
                        break

                valid_records: list[dict] = []
                external_ids: list[str] = []
                for record in records:
                    try:
                        ext_id = extractor.extract_external_id(record)
                    except ValueError as exc:
                        # Schema drift / malformed record: skip it, don't kill the run.
                        logger.warning("Skipping record on page %d: %s", page_index, exc)
                        run.records_skipped += 1
                        continue
                    valid_records.append(record)
                    external_ids.append(ext_id)

                saved = await destination.save(
                    valid_records,
                    source_name=config.name,
                    external_ids=external_ids,
                    run_id=run.id,
                )

                run.pages_fetched += 1
                run.records_fetched += len(records)
                run.records_saved += saved
                run.last_checkpoint = str(current_request.url)
                await session.commit()

                next_request = pagination_strategy.next_request(
                    base_request, current_request, response_json, response, page_index
                )

                # Stop if the page returned nothing and there's no explicit
                # "has more" signal -- guards against endless empty pages.
                if not records and next_request is not None:
                    logger.info(
                        "Page %d for source '%s' returned no records; stopping pagination.",
                        page_index, config.name,
                    )
                    break

                current_request = next_request
                page_index += 1

        finally:
            run.finished_at = dt.datetime.utcnow()
            run.status = "failed" if had_error and run.records_saved == 0 else (
                "partial" if had_error else "success"
            )
            await session.commit()
            await session.refresh(run)

        return run

    def _build_base_request(self, config: SourceConfig) -> httpx.Request:
        url = config.base_url.rstrip("/") + config.request.path
        return httpx.Request(
            config.request.method,
            url,
            headers=config.request.headers,
            params=config.request.query_params,
        )
