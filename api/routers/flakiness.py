from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from api.db.database import get_db
from api.models.schemas import FlakinessScore
from api.services import flakiness_service

router = APIRouter()


@router.get("/flakiness", response_model=list[FlakinessScore])
async def get_flakiness(db: AsyncSession = Depends(get_db)):
    return await flakiness_service.list_flakiness(db)
