"""
SQLAlchemy models. Schema is intentionally source-agnostic: raw_records
stores the full JSON payload of every ingested record plus lineage columns,
so it never needs to change shape when a new source is added.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_name: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="queued")  # queued/running/success/partial/failed
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    records_fetched: Mapped[int] = mapped_column(Integer, default=0)
    records_saved: Mapped[int] = mapped_column(Integer, default=0)
    records_skipped: Mapped[int] = mapped_column(Integer, default=0)
    last_checkpoint: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)

    records: Mapped[list["RawRecord"]] = relationship(back_populates="run")


class RawRecord(Base):
    __tablename__ = "raw_records"
    __table_args__ = (
        UniqueConstraint("source_name", "external_id", name="uq_source_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_name: Mapped[str] = mapped_column(String, index=True)
    external_id: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"))
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    run: Mapped["IngestionRun"] = relationship(back_populates="records")
