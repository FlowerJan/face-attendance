"""Statistics controller — summary, coverage, face count."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from api.deps import get_db_session
from service import stats_service

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/summary", summary="汇总统计（学生总数、已录入人数等）")
def get_summary(db: Session = Depends(get_db_session)):
    return stats_service.get_summary(db)


@router.get("/face-coverage", summary="人脸录入覆盖率（已录入 / 总人数）")
def get_face_coverage(db: Session = Depends(get_db_session)):
    return stats_service.get_face_coverage(db)


@router.get("/face-count", summary="脸照条数口径统计（1张/多张/孤儿照）")
def get_face_count(db: Session = Depends(get_db_session)):
    return stats_service.get_face_count(db)
