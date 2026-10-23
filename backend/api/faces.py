"""Face controller — enroll + list + delete."""
from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session
from api.deps import get_db_session, read_image_upload
from service import face_service

router = APIRouter(prefix="/api", tags=["faces"])


@router.post("/students/{student_id}/faces")
async def enroll_face(
    student_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db_session),
):
    image_bytes = await read_image_upload(file)
    return face_service.enroll_face(db, student_id, image_bytes)


@router.post("/faces/preview")
async def preview_face_quality(file: UploadFile = File(...)):
    """质量预检 —— 只诊断不入库，供前端在提交录入前展示。"""
    image_bytes = await read_image_upload(file)
    return face_service.preview_quality(image_bytes)


@router.get("/students/{student_id}/faces")
def get_faces(student_id: int, db: Session = Depends(get_db_session)):
    return face_service.get_student_faces(db, student_id)


@router.delete("/faces/{face_id}")
def delete_face(face_id: int, db: Session = Depends(get_db_session)):
    return face_service.delete_face(db, face_id)
