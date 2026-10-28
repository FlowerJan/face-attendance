"""M5（人脸识别）改进单元测试 —— 对应参考项目 M5 两条硬门槛。

覆盖点（均无需 insightface 模型 / 真实 Milvus，全部 monkeypatch 掉外部依赖）：
1. 硬门槛①：已失效 / 孤儿人脸不能参与 1:N 匹配
   - search_similar 的命中结果会被有效人脸索引过滤掉不在集合内的 face_id
2. 硬门槛②：诊断字段必须落库，且可被 M6（阈值标定）复盘
   - checkin 会把 top2_similarity / margin / ambiguous 写进 attendance 记录
3. 三态判定显式化：ambiguous 命中时 decision="ambiguous"
"""
import types

import numpy as np
import pytest

from repository import face_repo
from service import attendance_service as svc


# ---------------------------------------------------------------------------
# 1. 有效人脸索引过滤
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, hits):
        # 模拟 pymilvus 的 results[0] 列表
        self._hits = hits

    def __bool__(self):
        return bool(self._hits)

    def __getitem__(self, i):
        return self._hits


class _FakeHit:
    def __init__(self, face_id, student_id, sim):
        self.entity = types.SimpleNamespace(
            get=lambda k: {"face_id": face_id, "student_id": student_id}.get(k)
        )
        self.distance = sim


def _fake_client(hits):
    class C:
        def search(self, **kwargs):
            return _FakeResult(hits)
    return C()


def test_inactive_face_excluded_from_match(monkeypatch):
    """孤儿 / 已失效 face_id 不在 active 索引内 → 被 search_similar 过滤掉。"""
    face_repo._ACTIVE_FACE_IDS = {11, 12}  # 只有这两个有效
    monkeypatch.setattr(face_repo, "get_client",
                        lambda: _fake_client([
                            _FakeHit(11, 7, 0.93),
                            _FakeHit(99, 8, 0.91),   # 孤儿向量：SQLite 已删但 Milvus 还在
                            _FakeHit(12, 7, 0.88),
                        ]))

    hits = face_repo.search_similar(np.zeros(512), top_k=5)
    got_ids = [h["face_id"] for h in hits]
    assert 99 not in got_ids, "已失效人脸不应参与匹配"
    assert got_ids == [11, 12]


def test_only_active_false_keeps_all(monkeypatch):
    """only_active=False 时不过滤（录入实时查重用，避免漏掉自己）。"""
    face_repo._ACTIVE_FACE_IDS = {11}
    monkeypatch.setattr(face_repo, "get_client",
                        lambda: _fake_client([_FakeHit(99, 8, 0.91)]))
    hits = face_repo.search_similar(np.zeros(512), top_k=5, only_active=False)
    assert [h["face_id"] for h in hits] == [99]


# ---------------------------------------------------------------------------
# 2. 诊断字段落库 + 三态判定
# ---------------------------------------------------------------------------

def _fake_face(embedding):
    return types.SimpleNamespace(embedding=embedding)


def _pass_gate():
    class G:
        def check(self, *a, **k):
            return types.SimpleNamespace(
                passed=True, reason="", rule="", to_dict=lambda: {})
    return G()


class _FakeStudent:
    def __init__(self, sid=7):
        self.id = sid
        self.student_no = "2023001"
        self.name = "张三"
        self.class_name = "计算机23-1"
        self.face_status = 1


class _FakeRecord:
    def __init__(self, **kw):
        self.id = 1
        self.student_id = kw["student_id"]
        self.face_id = kw["face_id"]
        self.similarity = kw["similarity"]
        self.top2_similarity = kw.get("top2_similarity")
        self.margin = kw.get("margin")
        self.ambiguous = kw.get("ambiguous", 0)
        self.checkin_date = kw["checkin_date"]
        self.checkin_at = "2026-01-01 08:00:00"


def test_diagnostics_persisted_and_ambiguous_flagged(monkeypatch, db_session):
    """top1/top2 咬得很紧 → margin 落库、ambiguous=1、decision=ambiguous。"""
    emb = np.zeros(512, dtype=np.float32)
    monkeypatch.setattr(svc, "detect_faces", lambda img: [_fake_face(emb)])
    monkeypatch.setattr(svc, "_quality_gate", _pass_gate())
    monkeypatch.setattr(svc.cv2, "imdecode",
                        lambda buf, flag: np.zeros((100, 100, 3), np.uint8))

    captured = {}

    def fake_search(embedding, top_k=5, only_active=True, exclude_student_id=None):
        # top1 学号7，top2 学号9（不同人）且相似度咬得紧 → 歧义
        pool = [
            {"face_id": 11, "student_id": 7, "similarity": 0.80},
            {"face_id": 20, "student_id": 9, "similarity": 0.76},
        ]
        if exclude_student_id is not None:
            pool = [h for h in pool if h["student_id"] != exclude_student_id]
        return pool[:top_k]

    def fake_create(db, student_id, face_id, similarity, checkin_date, *,
                    top2_similarity=None, margin=None, ambiguous=0):
        captured.update(dict(student_id=student_id, face_id=face_id,
                             similarity=similarity, top2_similarity=top2_similarity,
                             margin=margin, ambiguous=ambiguous,
                             checkin_date=checkin_date))
        return _FakeRecord(student_id=student_id, face_id=face_id,
                           similarity=similarity, top2_similarity=top2_similarity,
                           margin=margin, ambiguous=ambiguous,
                           checkin_date=checkin_date)

    monkeypatch.setattr(face_repo, "search_similar", fake_search)
    monkeypatch.setattr(svc.student_repo, "get_by_id", lambda db, sid: _FakeStudent(sid))
    monkeypatch.setattr(svc.attendance_repo, "get_by_student_and_date",
                        lambda db, sid, d: None)
    monkeypatch.setattr(svc.attendance_repo, "create_record", fake_create)

    result = svc.checkin(db_session, _img_bytes())

    # 三态判定
    assert result["data"]["decision"] == "ambiguous"
    assert result["data"]["recognized"] is True
    # 诊断字段落库
    assert captured["margin"] == 0.04, captured
    assert captured["top2_similarity"] == 0.76
    assert captured["ambiguous"] == 1
    # 召回数据里也带诊断字段
    rec = result["data"]["record"]
    assert rec["margin"] == 0.04
    assert rec["ambiguous"] is True


def test_diagnostics_clean_when_far_apart(monkeypatch, db_session):
    """top2 离得很远 → 不算歧义，ambiguous=0，margin 仍落库。"""
    emb = np.zeros(512, dtype=np.float32)
    monkeypatch.setattr(svc, "detect_faces", lambda img: [_fake_face(emb)])
    monkeypatch.setattr(svc, "_quality_gate", _pass_gate())
    monkeypatch.setattr(svc.cv2, "imdecode",
                        lambda buf, flag: np.zeros((100, 100, 3), np.uint8))

    captured = {}

    def fake_search(embedding, top_k=5, only_active=True, exclude_student_id=None):
        pool = [
            {"face_id": 11, "student_id": 7, "similarity": 0.88},
            {"face_id": 20, "student_id": 9, "similarity": 0.31},
        ]
        if exclude_student_id is not None:
            pool = [h for h in pool if h["student_id"] != exclude_student_id]
        return pool[:top_k]

    monkeypatch.setattr(face_repo, "search_similar", fake_search)
    monkeypatch.setattr(svc.student_repo, "get_by_id", lambda db, sid: _FakeStudent(sid))
    monkeypatch.setattr(svc.attendance_repo, "get_by_student_and_date",
                        lambda db, sid, d: None)
    monkeypatch.setattr(svc.attendance_repo, "create_record",
                        lambda *a, **k: _capture_and_record(captured, *a, **k))

    result = svc.checkin(db_session, _img_bytes())
    assert result["data"]["decision"] == "recognized"
    assert captured["ambiguous"] == 0
    assert captured["margin"] == 0.57


def test_margin_survives_own_faces_filling_topk_window(monkeypatch, db_session):
    """关键回归：top1 自己录了多张底片、占满 top3 窗口时，margin 仍要算得出来。

    背景（真 bug）：旧实现取回 top3 后在窗口里找"另一个人"，
    而同一学生常录多张底片 → top3 全是他自己 → 异人不在窗口 →
    margin 恒为 None → ambiguous 永远 False → 歧义检测静默失效。
    现在改为在 Milvus 层 exclude top1 学号后重新检索。
    """
    emb = np.zeros(512, dtype=np.float32)
    monkeypatch.setattr(svc, "detect_faces", lambda img: [_fake_face(emb)])
    monkeypatch.setattr(svc, "_quality_gate", _pass_gate())
    monkeypatch.setattr(svc.cv2, "imdecode",
                        lambda buf, flag: np.zeros((100, 100, 3), np.uint8))

    captured = {}
    seen_filters = []

    def fake_search(embedding, top_k=5, only_active=True, exclude_student_id=None):
        seen_filters.append(exclude_student_id)
        # 库中：学生7 有 3 张底片（占满 top3），学生9 有 1 张且咬得紧
        pool = [
            {"face_id": 11, "student_id": 7, "similarity": 1.00},
            {"face_id": 12, "student_id": 7, "similarity": 0.99},
            {"face_id": 13, "student_id": 7, "similarity": 0.98},
            {"face_id": 20, "student_id": 9, "similarity": 0.93},
        ]
        if exclude_student_id is not None:
            pool = [h for h in pool if h["student_id"] != exclude_student_id]
        return pool[:top_k]

    monkeypatch.setattr(face_repo, "search_similar", fake_search)
    monkeypatch.setattr(svc.student_repo, "get_by_id", lambda db, sid: _FakeStudent(sid))
    monkeypatch.setattr(svc.attendance_repo, "get_by_student_and_date",
                        lambda db, sid, d: None)
    monkeypatch.setattr(svc.attendance_repo, "create_record",
                        lambda *a, **k: _capture_and_record(captured, *a, **k))

    result = svc.checkin(db_session, _img_bytes())

    # margin 必须算出来（1.00 - 0.93 = 0.07），而不是 None
    assert captured["margin"] == 0.07, captured
    assert captured["ambiguous"] == 1
    assert result["data"]["decision"] == "ambiguous"
    # 确认确实走了"排除 top1 学号"的检索
    assert 7 in seen_filters, seen_filters


def _capture_and_record(captured, db, student_id, face_id, similarity, checkin_date,
                        *, top2_similarity=None, margin=None, ambiguous=0):
    captured.update(dict(student_id=student_id, face_id=face_id, similarity=similarity,
                         top2_similarity=top2_similarity, margin=margin,
                         ambiguous=ambiguous, checkin_date=checkin_date))
    return _FakeRecord(student_id=student_id, face_id=face_id, similarity=similarity,
                       top2_similarity=top2_similarity, margin=margin,
                       ambiguous=ambiguous, checkin_date=checkin_date)


def _img_bytes():
    # cv2.imdecode 已被 monkeypatch 成恒返回有效数组，这里内容无所谓
    return b"dummy-image-bytes"
