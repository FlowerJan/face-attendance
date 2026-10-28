"""签到识别余量（margin）单元测试 —— 防止"两个人都像"时静默写记录。

margin 的定义：top1 相似度 − 与「另一个人」的最高相似度。

实现方式（关键）：在 Milvus 层用 filter=student_id != top1.student_id 排除
top1 所属学号后再检索一次，直接拿到异人最高相似度。
之所以不能"取回 top3 再在窗口里找异人"：同一学生常录多张底片，
top3 窗口会被他自己的底片占满 → 异人不在窗口 → 余量恒为 None → 歧义检测静默失效。
下面 test_margin_found_even_when_own_faces_fill_window 就是这条回归。

本文件不需要 Milvus / 模型 / 数据库，全部用 monkeypatch 打桩。
"""
from service import attendance_service as svc
from repository import face_repo


def _hit(student_id: int, sim: float, face_id: int = 1) -> dict:
    return {"face_id": face_id, "student_id": student_id, "similarity": sim}


def _patch_search(monkeypatch, by_student: dict[int, list[dict]], calls: list | None = None):
    """按学号返回检索结果；exclude_student_id 生效（模拟 Milvus filter）。"""

    def fake_search(embedding, top_k=5, only_active=True, exclude_student_id=None):
        if calls is not None:
            calls.append({"top_k": top_k, "exclude_student_id": exclude_student_id})
        pool = []
        for sid, hits in by_student.items():
            if exclude_student_id is not None and sid == exclude_student_id:
                continue  # 模拟 filter=student_id != X
            pool.extend(hits)
        pool.sort(key=lambda h: -h["similarity"])
        return pool[:top_k]

    monkeypatch.setattr(face_repo, "search_similar", fake_search)
    return fake_search


# ---------- _identify_margin ----------

def test_single_candidate_no_margin(monkeypatch):
    """库里只有一个人的底片：没有第二名可比，返回 None 而不是 0 或 1。"""
    _patch_search(monkeypatch, {1: [_hit(1, 0.92, face_id=11)]})
    assert svc._identify_margin(_emb(), _hit(1, 0.92, face_id=11)) is None


def test_same_student_other_faces_are_excluded(monkeypatch):
    """同一学生的其他底片不构成歧义——排除 top1 学号后异人自然不在结果里。"""
    _patch_search(monkeypatch, {7: [_hit(7, 0.93, face_id=11), _hit(7, 0.91, face_id=12)]})
    assert svc._identify_margin(_emb(), _hit(7, 0.93, face_id=11)) is None


def test_other_student_close_marks_ambiguous(monkeypatch):
    """异人咬得紧 → margin 小 → ambiguous=True，带中文提示。"""
    _patch_search(monkeypatch, {7: [_hit(7, 0.80, face_id=11)],
                                9: [_hit(9, 0.76, face_id=20)]})
    margin = svc._identify_margin(_emb(), _hit(7, 0.80, face_id=11))
    fields = svc._margin_fields(margin)
    assert fields["margin"] == 0.04
    assert fields["margin"] < svc.MARGIN_WARN, "该用例的前提是余量低于告警线"
    assert fields["ambiguous"] is True
    assert "人工复核" in fields["warning"]


def test_other_student_far_is_clean(monkeypatch):
    _patch_search(monkeypatch, {7: [_hit(7, 0.88, face_id=11)],
                                9: [_hit(9, 0.31, face_id=20)]})
    margin = svc._identify_margin(_emb(), _hit(7, 0.88, face_id=11))
    fields = svc._margin_fields(margin)
    assert fields["margin"] == 0.57
    assert fields["margin"] >= svc.MARGIN_WARN, "该用例的前提是余量高于告警线"
    assert fields["ambiguous"] is False
    assert "warning" not in fields


def test_margin_boundary_is_inclusive(monkeypatch):
    """恰好等于告警线：按 >= 判定，不算歧义。

    ★ 相似度按当前 MARGIN_WARN **动态构造**，不写死绝对值 ——
      告警线来自实测标定（见 docs/checkin-threshold-sweep.md），
      将来重新标定后数值会变，写死相似度会让本用例失效或误判。
    """
    _patch_search(monkeypatch, {7: [_hit(7, 0.70, face_id=11)],
                                9: [_hit(9, round(0.70 - svc.MARGIN_WARN, 4), face_id=20)]})
    margin = svc._identify_margin(_emb(), _hit(7, 0.70, face_id=11))
    fields = svc._margin_fields(margin)
    assert fields["margin"] == svc.MARGIN_WARN
    assert fields["ambiguous"] is False


def test_margin_just_below_warn_is_ambiguous(monkeypatch):
    """余量比告警线小一丁点：必须判为歧义（边界另一侧）。

    与上一条成对使用，锁住边界两侧的行为。
    """
    _patch_search(monkeypatch, {7: [_hit(7, 0.70, face_id=11)],
                                9: [_hit(9, round(0.70 - svc.MARGIN_WARN + 0.01, 4), face_id=20)]})
    margin = svc._identify_margin(_emb(), _hit(7, 0.70, face_id=11))
    fields = svc._margin_fields(margin)
    assert fields["margin"] < svc.MARGIN_WARN
    assert fields["ambiguous"] is True
    assert "人工复核" in fields["warning"]


def test_margin_found_even_when_own_faces_fill_window(monkeypatch):
    """关键回归：top1 自己录了多张底片时，异人仍必须能被找到。

    旧实现取回 top3 再在窗口里找异人——top3 被自己的底片占满，
    margin 恒为 None，歧义检测静默失效。现在改为 Milvus 层排除学号后检索。
    """
    calls: list = []
    _patch_search(monkeypatch, {
        7: [_hit(7, 1.00, face_id=11), _hit(7, 0.99, face_id=12), _hit(7, 0.98, face_id=13)],
        9: [_hit(9, 0.93, face_id=20)],
    }, calls=calls)

    margin = svc._identify_margin(_emb(), _hit(7, 1.00, face_id=11))
    assert margin == 0.07, margin
    assert margin < svc.MARGIN_WARN, "该用例的前提是余量低于告警线"
    assert svc._margin_fields(margin)["ambiguous"] is True
    # 确认走的是「排除 top1 学号」的检索，而不是普通 top_k 检索
    assert calls and calls[-1]["exclude_student_id"] == 7


def test_margin_none_when_search_fails(monkeypatch):
    """余量检索失败不应阻断签到：返回 None（按无歧义处理）。"""

    def boom(*a, **k):
        raise RuntimeError("milvus down")

    monkeypatch.setattr(face_repo, "search_similar", boom)
    assert svc._identify_margin(_emb(), _hit(7, 0.9, face_id=11)) is None


def test_margin_none_when_no_best(monkeypatch):
    """没有 top1（库里空）→ 无从比较。"""
    assert svc._identify_margin(_emb(), None) is None


# ---------- _margin_fields ----------

def test_none_margin_never_warns():
    """无从比较时既不标歧义也不给提示语，避免前端拿到半截数据。"""
    assert svc._margin_fields(None) == {"margin": None, "ambiguous": False}


def _emb():
    import numpy as np
    return np.zeros(512, dtype=np.float32)
