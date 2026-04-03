from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from api.db.models import CoverageModel


async def list_coverage(db: AsyncSession, limit: int = 100) -> list[CoverageModel]:
    result = await db.execute(
        select(CoverageModel).order_by(desc(CoverageModel.timestamp)).limit(limit)
    )
    return list(result.scalars().all())


async def upsert_coverage(db: AsyncSession, service: str, lines: float, branches: float, functions: float, statements: float) -> CoverageModel:
    from datetime import datetime
    report = CoverageModel(
        service=service,
        timestamp=datetime.utcnow(),
        lines=lines,
        branches=branches,
        functions=functions,
        statements=statements,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report
