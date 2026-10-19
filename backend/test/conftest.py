"""pytest fixture: 临时 SQLite Session，隔离生产 attendance.db。

会议"剥离建表职责至运维层"决议的体现：
- 测试环境**显式**在自己的临时 engine 上 `create_all`，绝不触碰生产 engine；
- 生产库的 engine 只创建一次（core.db 顶层），create_engine 是惰性的，不真正打开库文件。
"""
import os
import sys
import tempfile

# 把 backend/ 注入 sys.path，让 `from core.db` / `from models.student` 能 resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.db import Base
from models.student import Student  # noqa: F401  导入即注册到 Base.metadata


@pytest.fixture()
def db_session():
    """每个测试用例拿一个独立临时库的 Session，跑完自动销毁。"""
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
