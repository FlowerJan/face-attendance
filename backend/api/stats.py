"""Statistics controller — summary, coverage, face count."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from api.deps import get_db_session
from service import stats_service

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/summary")
def get_summary(db: Session = Depends(get_db_session)):
    return stats_service.get_summary(db)


@router.get("/face-coverage")
def get_face_coverage(db: Session = Depends(get_db_session)):
    return stats_service.get_face_coverage(db)


@router.get("/face-count")
def get_face_count(db: Session = Depends(get_db_session)):
    return stats_service.get_face_count(db)
