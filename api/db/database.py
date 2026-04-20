import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./aita.db")
_DB_ECHO = os.getenv("AITA_DB_ECHO", "false").lower() in ("1", "true", "yes")

engine = create_async_engine(DATABASE_URL, echo=_DB_ECHO)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with SessionLocal() as session:
        yield session


async def create_tables():
    async with engine.begin() as conn:
        from api.db import models as _  # noqa: F401 — ensure models are registered
        await conn.run_sync(Base.metadata.create_all)
