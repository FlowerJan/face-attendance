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


# 2) SQLite 连接级 PRAGMA：外键 + WAL + 写锁等待
#
# foreign_keys：默认关闭，会导致 faces 表 ON DELETE CASCADE 失效，
#   出现"学生已删除、脸照仍在"的孤儿数据，进而让统计接口算出负数。
#
# journal_mode=WAL：SQLite 出厂用 rollback journal，同一时刻只允许一个写事务，
#   且写会阻塞读。实测（scripts/bench_sqlite_write.py，50 并发写 500 事务）：
#       出厂默认   P95 ≈ 2.2 s，写吞吐 ≈ 190 TPS
#       开 WAL     P95 ≈ 0.28 s，写吞吐 ≈ 420 TPS
#   注意这里的失败形态不是报错，而是**延迟暴涨**——连接池会把并发排队消化掉，
#   监控上看不到 5xx，只会看到 P95 难看，所以很容易被忽略。
#
# busy_timeout=5000：拿不到写锁时先轮询等待 5 s 再失败。
#   出厂是 0，抢不到锁立刻抛 "database is locked"；设了之后绝大多数冲突变成
#   短暂等待而不是直接报错，等于给写路径加了一层退化保护。
#
# synchronous=NORMAL：WAL 模式下已能保证崩溃后的一致性，
#   不必每次提交都 fsync（FULL 会显著拖慢写），用极小的持久性余量换吞吐。
@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
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


def migrate_attendance_diagnostics():
    """为既有 attendance 表补齐 M5 诊断字段（top2_similarity / margin / ambiguous）。

    仅做 ADD COLUMN，且按"列已存在则跳过"的方式实现——对已建好的库无副作用，
    对老库则补齐，避免 checkin 落库时缺列报错。
    不改变任何已有数据。
    """
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("attendance")}
    needed = [
        ("top2_similarity", "FLOAT"),
        ("margin", "FLOAT"),
        ("ambiguous", "INTEGER NOT NULL DEFAULT 0"),
    ]
    with engine.begin() as conn:
        for name, ddl in needed:
            if name not in cols:
                conn.execute(text(f"ALTER TABLE attendance ADD COLUMN {name} {ddl}"))
                print(f"[migrate] attendance 已补列: {name}")
