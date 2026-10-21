"""Statistics service — summary, face coverage, face count distribution."""
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from models.student import Student
from models.face import Face


def get_summary(db: Session) -> dict:
    total = db.execute(select(func.count(Student.id))).scalar()

    by_class = {}
    rows = db.execute(
        select(Student.class_name, func.count(Student.id))
        .group_by(Student.class_name)
    ).all()
    for name, cnt in rows:
        if name:
            by_class[name] = cnt

    by_gender = {}
    rows = db.execute(
        select(Student.gender, func.count(Student.id))
        .group_by(Student.gender)
    ).all()
    for name, cnt in rows:
        if name:
            by_gender[name] = cnt

    by_major = {}
    rows = db.execute(
        select(Student.major, func.count(Student.id))
        .group_by(Student.major)
    ).all()
    for name, cnt in rows:
        if name:
            by_major[name] = cnt

    return {"code": 0, "data": {
        "total_students": total,
        "by_class": by_class,
        "by_gender": by_gender,
        "by_major": by_major,
    }}


def get_face_coverage(db: Session) -> dict:
    enrolled = db.execute(
        select(func.count(Student.id)).where(Student.face_status == 1)
    ).scalar()
    not_enrolled = db.execute(
        select(func.count(Student.id)).where(Student.face_status == 0)
    ).scalar()
    total = enrolled + not_enrolled
    rate = round(enrolled / total, 4) if total > 0 else 0

    return {"code": 0, "data": {
        "enrolled": enrolled,
        "not_enrolled": not_enrolled,
        "coverage_rate": rate,
    }}


def get_face_count(db: Session) -> dict:
    """统计学生持有 0 / 1 / 多张脸照的人数分布。

    只统计「仍存在的学生」的持有情况：即使历史数据里残留了孤儿脸照
    （例如外键级联未生效时删除学生留下的记录），也不会算出负数。
    """
    valid_ids = set(db.execute(select(Student.id)).scalars().all())
    total_students = len(valid_ids)

    face_counts = db.execute(
        select(Face.student_id, func.count(Face.id))
        .where(Face.student_id.in_(valid_ids) if valid_ids else False)
        .group_by(Face.student_id)
    ).all()
    face_map = {sid: cnt for sid, cnt in face_counts}

    zero_face = total_students - len(face_map)
    one_face = sum(1 for c in face_map.values() if c == 1)
    multiple = sum(1 for c in face_map.values() if c > 1)
    orphans = db.execute(
        select(func.count(Face.id)).where(
            Face.student_id.notin_(valid_ids) if valid_ids else True
        )
    ).scalar() or 0

    # 脸照元数据总条数（挂在有效学生名下的）。
    # 与 zero/one/multiple 的人数分布不同：one_face + multiple_faces 是「人数」，
    # 只有这里才是「条数」。对外取数需要用这个字段与向量库条数做一致性校验。
    total_faces = sum(face_map.values())

    return {"code": 0, "data": {
        "zero_face": zero_face,
        "one_face": one_face,
        "multiple_faces": multiple,
        "orphan_faces": orphans,
        "total_faces": total_faces,
    }}
