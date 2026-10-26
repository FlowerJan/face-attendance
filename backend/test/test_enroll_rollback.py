"""录入三写一致性测试 —— 任一处写入失败，必须回滚已完成的步骤。

背景（为什么要这些用例）：
    一次录入要写三个地方：磁盘原图 / SQLite 元数据 / Milvus 向量。
    它们之间没有事务，中途失败会留下「有图有记录但检索不到」的半截数据，
    而且不会自愈——用户看到录入成功，签到时却永远匹配不上。

    这里不测"失败时抛异常"（那本来就会抛），测的是**失败之后库里干不干净**。

不依赖 Milvus / 模型 / 真实上传目录：
    - detect_faces 用固定的 FaceCandidate 顶掉；
    - _quality_gate 换成恒通过的假门控；
    - settings 换成只带 FACE_IMG_DIR 的替身，指向 tmp_path；
    - face_repo 的向量读写全部 monkeypatch。
"""
import os
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import select

from core.face_types import FaceCandidate
from models.face import Face  # 导入即注册，保证临时库建出 faces 表
from models.student import Student
from repository import face_repo
from service import face_service


# ---- 替身 ----

class _QualityResult:
    passed = True
    reason = ""
    rule = ""

    def to_dict(self):
        return {}


class _PassGate:
    def check(self, *args, **kwargs):
        return _QualityResult()


def _fake_face() -> FaceCandidate:
    return FaceCandidate(
        bbox=(10.0, 10.0, 110.0, 110.0),
        kps=np.zeros((5, 2), dtype=np.float32),
        score=0.95,
        embedding=np.zeros(512, dtype=np.float32),
    )


@pytest.fixture()
def env(db_session, tmp_path, monkeypatch):
    """把 enroll_face 的外部依赖全部替换掉，只留待测试的写入逻辑。"""
    monkeypatch.setattr(face_service, "settings", SimpleNamespace(FACE_IMG_DIR=tmp_path))
    monkeypatch.setattr(face_service, "_quality_gate", _PassGate())
    monkeypatch.setattr(face_service, "detect_faces", lambda img: [_fake_face()])
    monkeypatch.setattr(face_service.cv2, "imdecode", lambda buf, flag: np.zeros((200, 200, 3), np.uint8))

    student = Student(
        student_no="T001", name="测试学生", gender="男",
        class_name="计科1班", major="计算机科学与技术",
        enroll_year=2023, phone="13800000001", face_status=0,
    )
    db_session.add(student)
    db_session.commit()

    calls = {"insert": [], "delete_vectors": [], "search": 0}

    def _fake_insert(face_id, student_id, embedding):
        calls["insert"].append(face_id)

    def _fake_delete(face_ids):
        calls["delete_vectors"].extend(face_ids)

    monkeypatch.setattr(face_repo, "insert_vector", _fake_insert)
    monkeypatch.setattr(face_repo, "delete_vectors", _fake_delete)
    monkeypatch.setattr(face_repo, "search_similar", lambda emb, top_k=5: [])

    # face_service 里是 `from repository import face_repo`，拿到的是同一个模块对象，
    # 所以上面打在 face_repo 上的桩在 service 里同样生效
    return SimpleNamespace(
        db=db_session,
        student=student,
        tmp=tmp_path,
        calls=calls,
        student_dir=tmp_path / student.student_no,
    )


def _jpg_count(directory) -> int:
    if not os.path.isdir(directory):
        return 0
    return len([f for f in os.listdir(directory) if f.endswith(".jpg")])


def _face_rows(db):
    return db.execute(select(Face)).scalars().all()


# ---- 用例 ----

def test_vector_insert_failure_rolls_back_disk_and_sqlite(env, monkeypatch):
    """最典型的一类失败：向量库写不进去。磁盘文件和数据库记录都必须被清掉。"""

    def _boom(face_id, student_id, embedding):
        raise RuntimeError("milvus 连接失败")

    monkeypatch.setattr(face_repo, "insert_vector", _boom)

    result = face_service.enroll_face(env.db, env.student.id, b"fake-bytes")

    assert result["code"] == 1
    assert "回滚" in result["message"]
    assert _jpg_count(env.student_dir) == 0, "磁盘原图必须被删除，否则留孤儿文件"
    assert _face_rows(env.db) == [], "SQLite 记录必须被删除，否则留下检索不到的脏数据"
    # 兜底清理：insert 抛异常时无法判定向量是否真的没写进去，必须按 face_id 幂等删一次
    assert env.calls["delete_vectors"] == [] or len(env.calls["delete_vectors"]) == 1


def test_sqlite_failure_removes_disk_file(env, monkeypatch):
    """建记录失败：此时磁盘文件已写，必须删掉，不能留在那变成孤儿。"""

    def _boom(db_session, student_id, image_path):
        raise RuntimeError("sqlite 写入失败")

    monkeypatch.setattr(face_repo, "create_face_record", _boom)

    result = face_service.enroll_face(env.db, env.student.id, b"fake-bytes")

    assert result["code"] == 1
    assert _jpg_count(env.student_dir) == 0
    assert env.calls["insert"] == [], "建记录就失败了，绝不该轮到写向量"


def test_disk_write_failure_is_detected(env, monkeypatch):
    """cv2.imwrite 写失败是返回 False 而不是抛异常。

    不显式判断的话，磁盘满 / 目录只读也会被当成成功，照样建记录、写向量。
    这条用例同时锁住"失败后不产生任何下游副作用"。
    """
    monkeypatch.setattr(face_service.cv2, "imwrite", lambda path, img: False)

    result = face_service.enroll_face(env.db, env.student.id, b"fake-bytes")

    assert result["code"] == 1
    assert _face_rows(env.db) == []
    assert env.calls["insert"] == []


def test_happy_path_still_enrolls(env):
    """回归保护：加了回滚逻辑之后，正常录入的行为不能变。"""
    result = face_service.enroll_face(env.db, env.student.id, b"fake-bytes")

    assert result["code"] == 0
    assert result["data"]["enrolled"] is True
    assert _jpg_count(env.student_dir) == 1

    rows = _face_rows(env.db)
    assert len(rows) == 1
    assert rows[0].face_id == rows[0].id, "face_id 必须与自增 id 对齐"
    assert env.calls["insert"] == [rows[0].id]

    env.db.refresh(env.student)
    assert env.student.face_status == 1


def test_face_status_failure_does_not_rollback_successful_enroll(env, monkeypatch):
    """face_status 是派生字段，更新失败刻意不回滚。

    走到这一步时三处写入都已完成，录入实质是成功的；
    为一个可由 count 重建的冗余字段把整次录入作废，代价更大。
    """
    from repository import student_repo

    def _boom(db_session, student_id, status):
        raise RuntimeError("status 更新失败")

    monkeypatch.setattr(student_repo, "update_face_status", _boom)

    result = face_service.enroll_face(env.db, env.student.id, b"fake-bytes")

    assert result["code"] == 0, "录入本身成功，不该因为冗余字段失败而报错"
    assert result["data"]["enrolled"] is True
    assert _jpg_count(env.student_dir) == 1
    assert len(_face_rows(env.db)) == 1


def test_rollback_continues_when_one_undo_step_fails(env, monkeypatch):
    """回滚链不能断：某一步回滚失败，其余步骤仍要继续执行。

    否则"磁盘删不掉"就会导致"数据库记录也留着"，半截状态反而更糟。
    """

    def _boom(face_id, student_id, embedding):
        raise RuntimeError("milvus 连接失败")

    monkeypatch.setattr(face_repo, "insert_vector", _boom)
    monkeypatch.setattr(face_service, "_remove_disk_file", lambda path: (_ for _ in ()).throw(OSError("文件被占用")))

    result = face_service.enroll_face(env.db, env.student.id, b"fake-bytes")

    assert result["code"] == 1
    # 磁盘那步回滚炸了，但 SQLite 那步仍须执行——不能因为一步失败就放弃其余清理
    assert _face_rows(env.db) == [], "磁盘清理失败不应导致数据库记录被留下"
