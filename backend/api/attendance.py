"""Attendance controller — face check-in, daily records, day summary."""
from fastapi import APIRouter, Depends, Query, UploadFile, File
from sqlalchemy.orm import Session

from api.deps import get_db_session
from service import attendance_service

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


@router.post("/checkin")
async def checkin(file: UploadFile = File(...), db: Session = Depends(get_db_session)):
    image_bytes = await file.read()
    return attendance_service.checkin(db, image_bytes)


@router.get("/records")
def list_records(
    date: str = Query(..., description="YYYY-MM-DD"),
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db_session),
):
    return attendance_service.list_records(db, date, page, page_size)


@router.get("/summary")
def day_summary(
    date: str = Query(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db_session),
):
    return attendance_service.day_summary(db, date)
