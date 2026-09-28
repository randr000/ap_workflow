"""Engine / session setup. SQLite by default; in-memory databases share one
connection so tests, the scenario runner and the benchmark stay isolated."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base

IN_MEMORY_URL = "sqlite://"


def make_engine(url: str = IN_MEMORY_URL) -> Engine:
    if url in ("sqlite://", "sqlite:///:memory:"):
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(url, connect_args={"check_same_thread": False})


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine, reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
