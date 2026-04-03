from datetime import datetime
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from api.db.models import FlakinessModel


async def list_flakiness(db: AsyncSession, limit: int = 100) -> list[FlakinessModel]:
    result = await db.execute(
        select(FlakinessModel).order_by(desc(FlakinessModel.score)).limit(limit)
    )
    return list(result.scalars().all())


async def record_result(db: AsyncSession, test_name: str, file_path: str, failed: bool) -> FlakinessModel:
    result = await db.execute(
        select(FlakinessModel).where(FlakinessModel.test_name == test_name)
    )
    record = result.scalar_one_or_none()

    if record is None:
        record = FlakinessModel(test_name=test_name, file_path=file_path, failure_count=0, run_count=0)
        db.add(record)

    record.run_count += 1
    if failed:
        record.failure_count += 1
    record.score = round((record.failure_count / record.run_count) * 100, 1)
    record.last_seen = datetime.utcnow()

    await db.commit()
    await db.refresh(record)
    return record
