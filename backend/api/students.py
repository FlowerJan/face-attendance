"""Student controller (corresponds to @RestController)."""
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from api.deps import get_db_session
from schemas.student import StudentCreate, StudentUpdate
from service import student_service
from core.enums import EnumContainer

router = APIRouter(prefix="/api/students", tags=["students"])


@router.get("", summary="学生列表（分页 / 关键字 / 班级筛选）")
def list_students(
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    class_name: str | None = None,
    db: Session = Depends(get_db_session),
):
    return student_service.list_students(db, page, page_size, keyword, class_name)


@router.get("/{student_id}", summary="查询单个学生")
def get_student(student_id: int, db: Session = Depends(get_db_session)):
    return student_service.get_student(db, student_id)


@router.post("", summary="新建学生（学号唯一 + 班级/专业枚举校验）")
def create_student(data: StudentCreate, db: Session = Depends(get_db_session)):
    return student_service.create_student(db, data)


@router.put("/{student_id}", summary="更新学生信息")
def update_student(student_id: int, data: StudentUpdate, db: Session = Depends(get_db_session)):
    return student_service.update_student(db, student_id, data)


@router.delete("/{student_id}", summary="删除学生（级联清理脸照、向量与签到记录）")
def delete_student(student_id: int, db: Session = Depends(get_db_session)):
    return student_service.delete_student(db, student_id)


@router.post("/import", summary="CSV 批量导入学生")
async def import_students(file: UploadFile = File(...), db: Session = Depends(get_db_session)):
    import tempfile, os
    suffix = os.path.splitext(file.filename)[1] or ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    try:
        return student_service.import_students_csv(db, tmp_path)
    finally:
        os.unlink(tmp_path)


# ---- enum endpoint ----
enum_router = APIRouter(prefix="/api/enums", tags=["enums"])


@enum_router.get("", summary="班级 / 专业枚举")
def get_enums():
    return {"code": 0, "data": {
        "classes": EnumContainer.get_classes(),
        "majors": EnumContainer.get_majors(),
    }}
