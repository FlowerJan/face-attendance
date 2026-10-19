"""Face controller — enroll + list + delete."""
from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.orm import Session
from api.deps import get_db_session
from service import face_service

router = APIRouter(prefix="/api", tags=["faces"])


@router.post("/students/{student_id}/faces")
async def enroll_face(
    student_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db_session),
):
    image_bytes = await file.read()
    return face_service.enroll_face(db, student_id, image_bytes)


@router.get("/students/{student_id}/faces")
def get_faces(student_id: int, db: Session = Depends(get_db_session)):
    return face_service.get_student_faces(db, student_id)


@router.delete("/faces/{face_id}")
def delete_face(face_id: int, db: Session = Depends(get_db_session)):
    return face_service.delete_face(db, face_id)
