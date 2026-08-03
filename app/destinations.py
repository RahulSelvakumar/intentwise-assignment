"""
Destination adapters: where ingested records are persisted. The orchestrator
only ever talks to the DestinationAdapter interface, so swapping/adding a
destination (e.g. S3) never touches auth/pagination/fetch logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RawRecord


class DestinationAdapter(ABC):
    @abstractmethod
    async def save(
        self,
        records: list[dict],
        *,
        source_name: str,
        external_ids: list[str],
        run_id: int,
    ) -> int:
        """Persist records, upserting on (source_name, external_id). Returns
        the number of records actually written."""


class RelationalDestination(DestinationAdapter):
    """Stores raw JSON payloads in the raw_records table (Postgres or
    SQLite), upserting on (source_name, external_id) so re-running an
    ingestion never creates duplicates."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(
        self,
        records: list[dict],
        *,
        source_name: str,
        external_ids: list[str],
        run_id: int,
    ) -> int:
        if not records:
            return 0

        rows = [
            {
                "source_name": source_name,
                "external_id": ext_id,
                "payload": record,
                "run_id": run_id,
            }
            for record, ext_id in zip(records, external_ids)
        ]

        dialect = self.session.bind.dialect.name
        insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
        stmt = insert_fn(RawRecord).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_name", "external_id"],
            set_={
                "payload": stmt.excluded.payload,
                "run_id": stmt.excluded.run_id,
            },
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return len(rows)


class S3Destination(DestinationAdapter):
    """Stub adapter demonstrating that adding a new destination requires
    implementing this one interface -- no orchestrator changes needed.
    Not implemented for the take-home; would write one object per record
    (or batched NDJSON) to an S3 bucket via boto3/aioboto3."""

    def __init__(self, bucket: str, prefix: str = ""):
        self.bucket = bucket
        self.prefix = prefix

    async def save(
        self,
        records: list[dict],
        *,
        source_name: str,
        external_ids: list[str],
        run_id: int,
    ) -> int:
        raise NotImplementedError(
            "S3Destination is a stub illustrating extensibility; implement with "
            "aioboto3 to enable object-storage destinations."
        )


def build_destination(destination_type: str, session: AsyncSession, **kwargs: Any) -> DestinationAdapter:
    if destination_type == "database":
        return RelationalDestination(session)
    if destination_type == "s3":
        return S3Destination(**kwargs)
    raise ValueError(f"Unsupported destination type: {destination_type}")
