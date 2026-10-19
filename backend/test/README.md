# 学生数据层全查询测试 demo

会议要求："在 test 文件夹下编写学生的全查询测试用例 demo"。

## 怎么跑

在 `backend/` 目录下：

```bash
# 方式 1：直接跑（无需 pytest，venv 已有 SQLAlchemy 即可）
.venv/Scripts/python.exe test/test_student_repo.py

# 方式 2：用 pytest（需要先装：pip install pytest）
.venv/Scripts/python.exe -m pip install pytest
.venv/Scripts/python.exe -m pytest test/ -v
```

## 测试覆盖的 4 个场景

| 场景 | 验证点 | 关键断言 |
|------|--------|----------|
| 1. 空表全查询 | `list_paginated` 在空表上的返回 | `items == []` 且 `total == 0` |
| 2. 插入 3 条后全查询 | 主场景：CRUD 的 R（全列表） | `total == 3`，姓名集合一致 |
| 3. 分页 `page=2 / page_size=2` | `offset/limit` 翻页 | `total == 5`，第 2 页返回 2 条 |
| 4. 模糊查询 `keyword='张'` | `name / student_no` 的 LIKE | `total == 2`，所有项都含"张" |

## 设计要点（落实会议决议）

- **隔离生产库**：每个场景用 `tempfile.mkstemp` 创建独立临时 `.db` 文件，跑完 `unlink` 删除，绝不动 `attendance.db`。
- **显式建表**：测试 fixture 自己 `Base.metadata.create_all(临时 engine)`，不在 import 时触发；生产 engine 只创建对象不真正打开库文件，体现"建表归运维层"。
- **绕开 EnumContainer 校验**：`StudentCreate` 不传 `class_name/major`，避免在没 load CSV 时被拒（验证的是数据层 CRUD，不是 schema 校验）。
- **双模式**：`python ... test_student_repo.py` 走 `main()`；`pytest` 自动收集 `test_*` 函数，由 `conftest.db_session` fixture 注入 Session。
