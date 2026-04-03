from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from api.db.database import get_db
from api.models.schemas import CoverageReport
from api.services import coverage_service

router = APIRouter()


@router.get("/coverage", response_model=list[CoverageReport])
async def get_coverage(db: AsyncSession = Depends(get_db)):
    return await coverage_service.list_coverage(db)
