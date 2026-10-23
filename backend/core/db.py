"""SQLAlchemy engine and session factory.

数据层基础设施（对应会议"剥离建表职责"决议）：
- 本模块只负责：① 创建 engine（连接 SQLite 文件）② 注册 PRAGMA ③ SessionLocal 工厂 ④ Base 元类 ⑤ get_db FastAPI 依赖。
- 建表（create_all）已剥离至运维层：生产环境由迁移脚本/DBA 在部署阶段建表，避免程序启动时自动改库结构。
- 测试用例若需要表结构，应在测试 fixture 内自建临时 engine + Base.metadata.create_all(临时 engine)，不依赖本模块。
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from core.config import settings

# 1) 引擎：长连接池 + 跨线程访问（FastAPI 多 worker 场景）
engine = create_engine(
    settings.SQLITE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


# 2) SQLite 外键开关：默认关闭，会导致 faces 表 ON DELETE CASCADE 失效，
#    出现"学生已删除、脸照仍在"的孤儿数据，进而让统计接口算出负数。
@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


# 3) 会话工厂：业务层每次请求从 SessionLocal() 取一个独立 Session
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# 4) ORM 基类：所有 Model 继承它，自动注册到 Base.metadata
class Base(DeclarativeBase):
    pass


# 5) FastAPI 依赖注入：路由函数声明 db: Session = Depends(get_db) 即可拿到会话
def get_db():
    """FastAPI dependency: yield a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ────────────────────────────────────────────────────────────
# 6) 运维层入口：建表（create_all）
# 会议"剥离建表职责至运维层"决议：
#   - 本函数不在 import 时触发，也禁止在请求路径里调用；
#   - 仅由 main.py 启动钩子、scripts/seed_demo.py 等开发/部署动作显式调用；
#   - 生产部署应替换为迁移工具（Alembic/SQL 迁移脚本）。
# ────────────────────────────────────────────────────────────
def init_db():
    """Create all tables. Only for dev/ops, not for runtime request path."""
    # import models so they register on Base.metadata
    from models import student, face, notice, attendance  # noqa: F401
    Base.metadata.create_all(bind=engine)
