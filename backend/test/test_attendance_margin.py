"""签到识别余量（margin）单元测试 —— 防止"两个人都像"时静默写记录。

margin 是一个纯函数（只看 1:N 检索返回的 hits），所以这里不需要 Milvus、
不需要模型、不需要数据库，直接喂 hits 列表即可。

覆盖点：
1. 库中只有一个人 → 无从比较，返回 None（不误报歧义）
2. top2 是同一个人的另一张底片 → 同样不算歧义，返回 None（关键回归点）
3. top2 是另一个人且咬得紧 → ambiguous=True，带中文提示
4. top2 是另一个人但拉开差距 → ambiguous=False
5. 边界值：恰好等于告警线不算歧义（>= 判定）
"""
from service import attendance_service as svc


def _hit(student_id: int, sim: float, face_id: int = 1) -> dict:
    return {"face_id": face_id, "student_id": student_id, "similarity": sim}


# ---------- _identify_margin ----------

def test_single_candidate_no_margin():
    """库里只有一个人的底片：没有第二名可比，返回 None 而不是 0 或 1。"""
    assert svc._identify_margin([_hit(1, 0.92)]) is None


def test_same_student_second_is_not_ambiguity():
    """同一学生的另一张底片不构成歧义——这是最容易写错的一条。"""
    hits = [_hit(7, 0.93, face_id=11), _hit(7, 0.91, face_id=12)]
    assert svc._identify_margin(hits) is None


def test_other_student_close_marks_ambiguous():
    hits = [_hit(7, 0.80, face_id=11), _hit(9, 0.76, face_id=20)]
    fields = svc._margin_fields(svc._identify_margin(hits))
    assert fields["margin"] == 0.04
    assert fields["ambiguous"] is True
    assert "人工复核" in fields["warning"]


def test_other_student_far_is_clean():
    hits = [_hit(7, 0.88, face_id=11), _hit(9, 0.31, face_id=20)]
    fields = svc._margin_fields(svc._identify_margin(hits))
    assert fields["margin"] == 0.57
    assert fields["ambiguous"] is False
    assert "warning" not in fields


def test_margin_boundary_is_inclusive():
    """恰好等于告警线：按 >= 判定，不算歧义。"""
    hits = [_hit(7, 0.70, face_id=11), _hit(9, 0.60, face_id=20)]
    fields = svc._margin_fields(svc._identify_margin(hits))
    assert fields["margin"] == svc.MARGIN_WARN
    assert fields["ambiguous"] is False


# ---------- _margin_fields ----------

def test_none_margin_never_warns():
    """无从比较时既不标歧义也不给提示语，避免前端拿到半截数据。"""
    assert svc._margin_fields(None) == {"margin": None, "ambiguous": False}
