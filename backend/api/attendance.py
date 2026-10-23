"""Attendance controller — face check-in, daily records, day summary."""
from fastapi import APIRouter, Depends, Query, UploadFile, File
from sqlalchemy.orm import Session

from api.deps import get_db_session, read_image_upload
from service import attendance_service

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


@router.post("/checkin", summary="人脸签到（拍照识别，当日幂等）")
async def checkin(file: UploadFile = File(...), db: Session = Depends(get_db_session)):
    image_bytes = await read_image_upload(file)
    return attendance_service.checkin(db, image_bytes)


@router.get("/records", summary="按日查询签到记录（分页）")
def list_records(
    date: str = Query(..., description="签到日期，格式 YYYY-MM-DD", examples=["2026-10-23"]),
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db_session),
):
    return attendance_service.list_records(db, date, page, page_size)


@router.get("/summary", summary="当日签到汇总（已签人数 / 总人数 / 签到率）")
def day_summary(
    date: str = Query(..., description="签到日期，格式 YYYY-MM-DD", examples=["2026-10-23"]),
    db: Session = Depends(get_db_session),
):
    return attendance_service.day_summary(db, date)
