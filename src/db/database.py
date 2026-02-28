"""
Hermit Purple 數據庫連接管理

用途：管理 SQLite 數據庫連接和會話
"""

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_config, get_env
from .models import Base

logger = logging.getLogger(__name__)


# 全局引擎和會話工廠
_engine = None
_SessionLocal = None


def get_database_url() -> str:
    """
    用途：獲取數據庫連接 URL
    
    @returns: SQLite 連接字符串
    """
    env = get_env()
    
    # 優先使用環境變量
    if env.database_url:
        return env.database_url
    
    # 使用配置文件
    config = get_config()
    db_path = Path(__file__).parent.parent.parent / config.database.path
    
    # 確保目錄存在
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    return f"sqlite:///{db_path}"


def get_engine():
    """
    用途：獲取數據庫引擎（單例）
    
    @returns: SQLAlchemy Engine
    """
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_database_url(),
            echo=False,
            connect_args={"check_same_thread": False},  # SQLite 特有
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """
    用途：獲取會話工廠（單例）
    
    @returns: sessionmaker 工廠
    """
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
        )
    return _SessionLocal


def _migrate_add_columns(engine) -> None:
    """Add new columns to existing tables (SQLite ALTER TABLE)."""
    import sqlalchemy
    with engine.connect() as conn:
        inspector = sqlalchemy.inspect(engine)

        # resources 表遷移
        res_cols = {col["name"] for col in inspector.get_columns("resources")}
        if "source_tier" not in res_cols:
            conn.execute(sqlalchemy.text("ALTER TABLE resources ADD COLUMN source_tier VARCHAR(20)"))
        if "citation_urls" not in res_cols:
            conn.execute(sqlalchemy.text("ALTER TABLE resources ADD COLUMN citation_urls JSON"))

        # reports 表遷移：加入 category 欄位以支持分類獨立報告
        if "reports" in inspector.get_table_names():
            report_cols = {col["name"] for col in inspector.get_columns("reports")}
            if "category" not in report_cols:
                conn.execute(sqlalchemy.text("ALTER TABLE reports ADD COLUMN category VARCHAR(100) NOT NULL DEFAULT ''"))
                # 加入複合唯一索引（SQLite 無法 DROP 舊的 column-level UNIQUE，
                # 需要 table rebuild 才能徹底移除，見 fix_db.py）
                try:
                    conn.execute(sqlalchemy.text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_report_week_category ON reports (week_start, category)"
                    ))
                except Exception as e:
                    logger.debug(f"Index creation skipped: {e}")

        conn.commit()


def init_db() -> None:
    """
    用途：初始化數據庫，創建所有表

    失敗：如果無法創建數據庫文件，拋出 OSError
    """
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    try:
        _migrate_add_columns(engine)
    except Exception as e:
        err_msg = str(e).lower()
        if "no such table" in err_msg or "does not exist" in err_msg:
            pass  # First run — create_all already handles it
        else:
            logger.error(f"DB migration failed: {e}")
            raise


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """
    用途：獲取數據庫會話（上下文管理器）
    
    使用方式：
        with get_db() as db:
            db.query(Resource).all()
    
    @returns: Session 對象
    """
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    else:
        db.commit()
    finally:
        db.close()


def get_db_session() -> Session:
    """
    用途：獲取數據庫會話（用於 FastAPI 依賴注入）
    
    @returns: Session 對象（需要手動關閉）
    """
    SessionLocal = get_session_factory()
    return SessionLocal()
