"""cluster_service.audit_duplicates 单元测试 —— 论文第 5 章双通道判定逻辑的回归防线。

测试策略（不碰 Milvus / 不跑模型）：
- 用固定种子的合成向量精确控制余弦相似度（b = sim*a + sqrt(1-sim^2)*noise），
  monkeypatch repository.face_repo.get_all_vectors 喂给审计函数；
- db.get(Student) 走 conftest 的临时 SQLite session，测试内插入 Student 行。

覆盖点对应论文数字：
1. 空库/单向量边界（返回统一结构）
2. 跨学生重复注册（duplicate_pairs，两两通道）
3. 同学生脏数据（dirty_pairs，两两通道）
4. 干净库零误报（审计侧"正常库 0 误报"的逻辑等价物）
5. 阈值敏感性（同一数据，动一个阈值看结果变化——变量隔离）
6. 聚类通道（suspicious_clusters，跨学生簇 = 强证据）
"""
import numpy as np
import pytest

from models.student import Student
from repository import face_repo
from service import cluster_service


# ---------- 合成向量工具 ----------

def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-12)


def make_pair(rng: np.random.Generator, sim: float):
    """返回 (a, b) 两个单位向量，其余弦相似度精确等于 sim。"""
    a = _unit(rng.normal(size=8))
    noise = rng.normal(size=8)
    noise = _unit(noise - (noise @ a) * a)  # 垂直于 a 的单位噪声
    b = sim * a + np.sqrt(max(0.0, 1.0 - sim * sim)) * noise
    return a, _unit(b)


def vectors_payload(items: list[tuple[int, int, np.ndarray]]) -> list[dict]:
    """[(face_id, student_id, embedding), ...] -> Milvus 拉取结果同构列表。"""
    return [{"face_id": fid, "student_id": sid, "embedding": emb} for fid, sid, emb in items]


@pytest.fixture()
def students(db_session):
    """插入 4 名学生并回填 id。"""
    rows = []
    for i in range(1, 5):
        s = Student(student_no=f"2024000{i}", name=f"学生{i}")
        db_session.add(s)
        rows.append(s)
    db_session.commit()
    return rows


@pytest.fixture()
def patch_vectors(monkeypatch):
    def _patch(items):
        monkeypatch.setattr(face_repo, "get_all_vectors", lambda: vectors_payload(items))
    return _patch


# ---------- 用例 ----------

def test_empty_library(db_session, patch_vectors):
    patch_vectors([])
    result = cluster_service.audit_duplicates(db_session)
    assert result["code"] == 0
    assert result["data"]["summary"]["total_vectors"] == 0
    assert "脸库为空" in result["data"]["note"]


def test_single_vector(db_session, students, patch_vectors):
    patch_vectors([(1, students[0].id, np.ones(8))])
    result = cluster_service.audit_duplicates(db_session)
    assert result["code"] == 0
    assert "无法进行两两比对" in result["data"]["note"]


def test_duplicate_registration(db_session, students, patch_vectors):
    """两个不同学生、同一张脸 => 两两通道报 1 对重复。"""
    rng = np.random.default_rng(42)
    a, b = make_pair(rng, 0.95)
    patch_vectors([(1, students[0].id, a), (2, students[1].id, b)])
    result = cluster_service.audit_duplicates(db_session)
    data = result["data"]
    assert data["summary"]["duplicate_pairs"] == 1
    assert data["duplicates"][0]["student_a"]["id"] == students[0].id
    assert data["duplicates"][0]["student_b"]["id"] == students[1].id


def test_dirty_data_same_student(db_session, students, patch_vectors):
    """同一学生两张差异过大的脸 => 两两通道报 1 对脏数据。"""
    rng = np.random.default_rng(7)
    a, b = make_pair(rng, 0.30)  # < same_student 0.40
    patch_vectors([(1, students[0].id, a), (2, students[0].id, b)])
    result = cluster_service.audit_duplicates(db_session)
    data = result["data"]
    assert data["summary"]["dirty_pairs"] == 1
    assert data["dirty_data"][0]["student"]["id"] == students[0].id
    assert data["summary"]["duplicate_pairs"] == 0  # 同学生不进重复判定


def test_clean_library_zero_false_positive(db_session, students, patch_vectors):
    """4 张脸：两两同人高相似、跨人近正交 => 零重复、零脏数据、零可疑簇。"""
    rng = np.random.default_rng(123)
    bases = [_unit(rng.normal(size=8)) for _ in range(2)]
    items = []
    fid = 1
    for si, base in enumerate(bases):
        noise = _unit(rng.normal(size=8))
        items.append((fid, students[si].id, base))
        fid += 1
        items.append((fid, students[si].id, _unit(0.9 * base + np.sqrt(1 - 0.81) * noise)))
        fid += 1
    patch_vectors(items)
    result = cluster_service.audit_duplicates(db_session)
    data = result["data"]
    assert data["summary"]["duplicate_pairs"] == 0
    assert data["summary"]["dirty_pairs"] == 0
    assert data["summary"]["suspicious_cluster_count"] == 0


def test_threshold_sensitivity(db_session, students, patch_vectors):
    """同一数据只动 cross 阈值：0.50 报 1 对、0.99 报 0 对——阈值决定结论而非数据固有。"""
    rng = np.random.default_rng(42)
    a, b = make_pair(rng, 0.95)
    patch_vectors([(1, students[0].id, a), (2, students[1].id, b)])
    r_low = cluster_service.audit_duplicates(db_session, cross_student_threshold=0.50)
    r_high = cluster_service.audit_duplicates(db_session, cross_student_threshold=0.99)
    assert r_low["data"]["summary"]["duplicate_pairs"] == 1
    assert r_high["data"]["summary"]["duplicate_pairs"] == 0


def test_suspicious_cluster(db_session, students, patch_vectors):
    """三个学生的脸完全相同 => 聚类通道给出跨学生可疑簇（强于两两的证据）。"""
    base = _unit(np.random.default_rng(5).normal(size=8))
    patch_vectors([(1, students[0].id, base), (2, students[1].id, base), (3, students[2].id, base)])
    result = cluster_service.audit_duplicates(db_session)
    data = result["data"]
    assert data["summary"]["suspicious_cluster_count"] == 1
    cluster = data["suspicious_clusters"][0]
    assert cluster["size"] == 3
    assert cluster["mixed_students"] is True
    assert len(cluster["students"]) == 3


def test_thresholds_echoed_in_result(db_session, students, patch_vectors):
    """返回结构必须回显所用阈值（论文表 5-x"结果依赖阈值"口径的程序保证）。"""
    patch_vectors([(1, students[0].id, np.ones(8)), (2, students[1].id, np.ones(8))])
    result = cluster_service.audit_duplicates(
        db_session, same_student_threshold=0.3, cross_student_threshold=0.6, cluster_distance=0.5
    )
    assert result["data"]["thresholds"] == {
        "same_student": 0.3, "cross_student": 0.6, "cluster_distance": 0.5,
    }
