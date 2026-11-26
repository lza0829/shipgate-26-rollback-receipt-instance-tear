from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  owner TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '启用' CHECK(status IN ('启用','停用'))
);
CREATE TABLE IF NOT EXISTS environments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  sort_order INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS app_env_versions (
  app_id INTEGER NOT NULL,
  env_id INTEGER NOT NULL,
  version TEXT NOT NULL DEFAULT '',
  updated_at TEXT,
  PRIMARY KEY(app_id, env_id),
  FOREIGN KEY(app_id) REFERENCES apps(id),
  FOREIGN KEY(env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS releases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  serial TEXT NOT NULL UNIQUE,
  app_id INTEGER NOT NULL,
  target_version TEXT NOT NULL,
  artifact_digest TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '待入闸'
    CHECK(status IN ('待入闸','已入闸','晋升中','已放行','回滚中','已回滚','已关闭')),
  pipeline_status TEXT NOT NULL DEFAULT '未回执'
    CHECK(pipeline_status IN ('未回执','绿','红')),
  current_env_id INTEGER,
  canary_ratio INTEGER NOT NULL DEFAULT 10,
  rollback_target_version TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  admitted_at TEXT,
  released_at TEXT,
  rolled_back_at TEXT,
  FOREIGN KEY(app_id) REFERENCES apps(id),
  FOREIGN KEY(current_env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS promote_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  release_id INTEGER NOT NULL,
  from_env_id INTEGER,
  to_env_id INTEGER NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('放行','拒绝')),
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(release_id) REFERENCES releases(id),
  FOREIGN KEY(from_env_id) REFERENCES environments(id),
  FOREIGN KEY(to_env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS release_windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  env_id INTEGER NOT NULL,
  open_at TEXT NOT NULL,
  close_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '窗开' CHECK(status IN ('窗开','窗关')),
  FOREIGN KEY(env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS rollback_windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  env_id INTEGER NOT NULL,
  open_at TEXT NOT NULL,
  close_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '窗开' CHECK(status IN ('窗开','窗关')),
  FOREIGN KEY(env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS instances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  app_id INTEGER NOT NULL,
  env_id INTEGER NOT NULL,
  version TEXT NOT NULL,
  health TEXT NOT NULL DEFAULT '探针绿' CHECK(health IN ('探针绿','探针红')),
  release_id INTEGER,
  residual_release_id INTEGER,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(app_id) REFERENCES apps(id),
  FOREIGN KEY(env_id) REFERENCES environments(id),
  FOREIGN KEY(release_id) REFERENCES releases(id),
  FOREIGN KEY(residual_release_id) REFERENCES releases(id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
"""

RELEASE_STATUSES = ("待入闸", "已入闸", "晋升中", "已放行", "回滚中", "已回滚", "已关闭")
PIPELINE_STATUSES = ("未回执", "绿", "红")
HEALTH_STATUSES = ("探针绿", "探针红")
WINDOW_STATUSES = ("窗开", "窗关")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # 写事务拿锁失败时排队等待，而不是立即抛 database is locked
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def db():
    """读事务/普通写事务连接，提交语义保持不变。"""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def write_tx():
    """
    串行写事务：BEGIN IMMEDIATE 立即拿 RESERVED 锁。
    回退写口、残留清理、探针写口等互斥面统一走这里，
    保证回退与实例心跳并发时结局确定、可重放。
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def residual_count(conn, release_id: int) -> int:
    """某发版单名下的回退残留实例行数——完成定义的唯一计量。"""
    return conn.execute(
        "SELECT COUNT(*) AS c FROM instances WHERE residual_release_id=?",
        (release_id,),
    ).fetchone()["c"]


def evaluate_rollback_convergence(conn, release_id: int) -> bool:
    """
    回退再收敛：残留清零时，在同一写事务内把发版单置为已回滚并翻转名册。
    名册只允许随收敛翻转，因此 已回滚 ⇔ 残留为0 ⇔ 名册为回退目标版本 恒同时成立。
    返回是否已收敛。
    """
    if residual_count(conn, release_id) > 0:
        return False
    r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    # 只有 回滚中->已回滚 的真实翻转才返回 True；早已回滚不算新收敛，避免重复审计
    if not r or r["status"] != "回滚中":
        return False
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE releases SET status='已回滚', rolled_back_at=? WHERE id=?",
        (now, release_id),
    )
    conn.execute(
        """
        INSERT INTO app_env_versions(app_id, env_id, version, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(app_id, env_id) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at
        """,
        (r["app_id"], r["current_env_id"], r["rollback_target_version"], now),
    )
    return True


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn) -> None:
    """对旧库补列，幂等；新库列已在 SCHEMA 内，跳过。"""
    release_cols = {r["name"] for r in conn.execute("PRAGMA table_info(releases)").fetchall()}
    if "rollback_target_version" not in release_cols:
        conn.execute(
            "ALTER TABLE releases ADD COLUMN rollback_target_version TEXT NOT NULL DEFAULT ''"
        )
    instance_cols = {r["name"] for r in conn.execute("PRAGMA table_info(instances)").fetchall()}
    if "residual_release_id" not in instance_cols:
        conn.execute("ALTER TABLE instances ADD COLUMN residual_release_id INTEGER")
    # 索引在补列之后统一建，新库旧库均幂等
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_instances_residual ON instances(residual_release_id)"
    )


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
