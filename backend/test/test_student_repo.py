"""学生数据层"全查询"测试用例 demo。

设计目标（会议"在 test 文件夹下编写学生的全查询测试用例 demo"）：
1. 引擎 + Session：使用临时 SQLite 文件，不污染生产 attendance.db；
2. 显式建表：Base.metadata.create_all(临时 engine) —— 体现"建表归运维层"的隔离；
3. 实例化实体类：通过 repository.create 插入若干 Student；
4. 调 repository.list_paginated 全查询，断言结果集。

两种跑法：
  - 没装 pytest：  python test/test_student_repo.py   → 走 main()，print PASS
  - 装了 pytest：  pytest test/test_student_repo.py    → 自动用 conftest.db_session fixture
"""
import os
import sys
import tempfile
from contextlib import contextmanager

# backend/ 注入 sys.path，让 core / models / schemas / repository 可被解析
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.db import Base
from models.student import Student  # noqa: F401  导入即注册到 Base.metadata
from schemas.student import StudentCreate
from repository import student_repo


# ─── 临时 Session 工具（main 模式用，等价于 conftest.db_session）────────
@contextmanager
def temp_session():
    """构造临时 SQLite Session，跑完销毁，隔离生产库。"""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_attendance_")
    os.close(fd)
    try:
        engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=engine)  # 仅在临时 engine 上建表
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        session = Session()
        try:
            yield session
        finally:
            session.close()
            engine.dispose()
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ─── 测试场景 ────────────────────────────────────────────────────────────
def test_empty_table(db_session):
    """场景 1：空表全查询 —— 应返回空列表、total=0。"""
    items, total = student_repo.list_paginated(db_session)
    assert items == [], f"空表应返回空列表，实际 {items}"
    assert total == 0, f"空表总数应为 0，实际 {total}"


def test_full_query(db_session):
    """场景 2：插入 3 条后全查询 —— 主场景（会议要求）。"""
    # class_name/major 不传 → 跳过 EnumContainer 的 CSV 校验
    # （未 load CSV 时合法值集为空，传值会被拒）
    student_repo.create(db_session, StudentCreate(student_no="2026001", name="张三"))
    student_repo.create(db_session, StudentCreate(student_no="2026002", name="李四"))
    student_repo.create(db_session, StudentCreate(student_no="2026003", name="王五"))

    items, total = student_repo.list_paginated(db_session)
    assert total == 3, f"总数应为 3，实际 {total}"
    assert len(items) == 3, f"列表长度应为 3，实际 {len(items)}"
    assert {s.name for s in items} == {"张三", "李四", "王五"}, "姓名集合应一致"
    return items


def test_pagination(db_session):
    """场景 3：分页 page=2 / page_size=2。"""
    for i in range(5):
        student_repo.create(
            db_session,
            StudentCreate(student_no=f"20260{i+1:02d}", name=f"学生{i+1}"),
        )

    items, total = student_repo.list_paginated(db_session, page=2, page_size=2)
    assert total == 5, f"总数应为 5，实际 {total}"
    assert len(items) == 2, f"第 2 页应 2 条，实际 {len(items)}"
    return items


def test_keyword_search(db_session):
    """场景 4：关键字模糊查询 keyword='张'。"""
    student_repo.create(db_session, StudentCreate(student_no="2026001", name="张三"))
    student_repo.create(db_session, StudentCreate(student_no="2026002", name="张无忌"))
    student_repo.create(db_session, StudentCreate(student_no="2026003", name="李四"))

    items, total = student_repo.list_paginated(db_session, keyword="张")
    assert total == 2, f"含'张'的应 2 条，实际 {total}"
    assert all("张" in s.name for s in items), "返回项姓名应都含'张'"
    return items


def _print_items(items):
    for s in items:
        print(f"    id={s.id}  student_no={s.student_no}  name={s.name}")


# ─── 主入口（无 pytest 也能跑）──────────────────────────────────────────
def main():
    print("=" * 64)
    print("学生数据层全查询 demo  (临时库，不污染 attendance.db)")
    print("=" * 64)

    cases = [
        ("场景 1：空表全查询", test_empty_table),
        ("场景 2：插入 3 条后全查询", test_full_query),
        ("场景 3：分页 page=2 / page_size=2", test_pagination),
        ("场景 4：模糊查询 keyword='张'", test_keyword_search),
    ]
    for label, fn in cases:
        print(f"\n--- {label} ---")
        with temp_session() as session:
            result = fn(session)
            if isinstance(result, list):
                _print_items(result)
        print("  ✓ PASS")

    print("\n" + "=" * 64)
    print("全部场景通过 ✓")
    print("=" * 64)


if __name__ == "__main__":
    main()
