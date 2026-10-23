"""Face controller — enroll + list + delete."""
from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session
from api.deps import get_db_session, read_image_upload
from service import face_service

router = APIRouter(prefix="/api", tags=["faces"])


@router.post("/students/{student_id}/faces", summary="录入人脸（检测→质量门控→入库）")
async def enroll_face(
    student_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db_session),
):
    image_bytes = await read_image_upload(file)
    return face_service.enroll_face(db, student_id, image_bytes)


@router.post("/faces/preview", summary="质量预检（只诊断不入库，提交前先探一次）")
async def preview_face_quality(file: UploadFile = File(...)):
    """质量预检 —— 只诊断不入库，供前端在提交录入前展示。"""
    image_bytes = await read_image_upload(file)
    return face_service.preview_quality(image_bytes)


@router.get("/students/{student_id}/faces", summary="查询某学生的全部脸照")
def get_faces(student_id: int, db: Session = Depends(get_db_session)):
    return face_service.get_student_faces(db, student_id)


@router.delete("/faces/{face_id}", summary="删除单张脸照（磁盘+向量库+SQLite 三处清理）")
def delete_face(face_id: int, db: Session = Depends(get_db_session)):
    return face_service.delete_face(db, face_id)
