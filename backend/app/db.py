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
  retired_at TEXT,
  retired_version TEXT NOT NULL DEFAULT '',
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
  retired_by_release_id INTEGER,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(app_id) REFERENCES apps(id),
  FOREIGN KEY(env_id) REFERENCES environments(id),
  FOREIGN KEY(release_id) REFERENCES releases(id),
  FOREIGN KEY(residual_release_id) REFERENCES releases(id),
  FOREIGN KEY(retired_by_release_id) REFERENCES releases(id)
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
    """某发版单名下的回退残留实例行数——残留旁读的唯一计量。"""
    return conn.execute(
        "SELECT COUNT(*) AS c FROM instances WHERE residual_release_id=?",
        (release_id,),
    ).fetchone()["c"]


def gap_rows(conn, release_id: int) -> list:
    """
    本单回退缺口行：同应用同当前环境内、版本不等于回退目标版本的在跑实例行。

    豁免只给“已被更晚的晋升接管、且该晋升单自身处在正常在途态”的行：
    那是新发版在滚，不是本单回退的残留；但若更晚的晋升单本身停在回滚中，本单依旧有缺口。
    """
    r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    if not r or not r["current_env_id"] or not r["rollback_target_version"]:
        return []
    rows = conn.execute(
        """
        SELECT i.id, i.version, i.app_id, i.env_id,
               i.residual_release_id, i.retired_by_release_id,
               (SELECT rr.status FROM releases rr WHERE rr.id=i.release_id) AS holder_status,
               i.release_id AS holder_id
        FROM instances i
        WHERE i.app_id=? AND i.env_id=? AND i.version!=?
        """,
        (r["app_id"], r["current_env_id"], r["rollback_target_version"]),
    ).fetchall()
    out = []
    for x in rows:
        if x["holder_id"] is not None and x["holder_id"] != release_id \
                and x["holder_status"] in ("已入闸", "晋升中", "已放行"):
            continue  # 被更晚的在途发版接管，不属本单缺口
        out.append(x)
    return out


def offtarget_live_count(conn, release_id: int) -> int:
    """本单回退缺口行数（在跑旧版本实例），残留标被抹、退役标缺位都逃不出这里。"""
    return len(gap_rows(conn, release_id))


def rollback_gap(conn, release_id: int) -> dict:
    """
    回滚缺口三源同核 + 审计重放：残留旁读、退役标、批次对照，外加审计里是否存在
    与库内状态相矛盾的“收敛完成”回执（假干净回执）。
    任一源不为 0/缺位/不一致，即不许宣称“已回滚”。
    """
    r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    if not r or not r["current_env_id"] or not r["rollback_target_version"]:
        return {
            "发版单编号": release_id,
            "回退目标版本": r["rollback_target_version"] if r else "",
            "状态": r["status"] if r else "",
            "残留旁读": 0,
            "在跑旧版本数": 0,
            "退役标缺位": 0,
            "批次对照一致": True,
            "审计重放矛盾": 0,
            "缺口": 0,
            # 没进入过回滚流程的单子无所谓“干净宣称”
            "干净可宣称": False,
        }
    rows = gap_rows(conn, release_id)
    residual_rows = sum(1 for x in rows if x["residual_release_id"] == release_id)
    missing_retire = sum(1 for x in rows if not x["retired_by_release_id"])
    # 批次对照：该单名下实例与发版单的 应用/环境/非目标版本 必须逐行对得上
    mismatch = sum(
        1 for x in conn.execute(
            """
            SELECT i.app_id, i.env_id, i.version, rr.app_id AS 单应用, rr.current_env_id AS 单环境,
                   rr.rollback_target_version AS 单目标
            FROM instances i JOIN releases rr ON rr.id = i.residual_release_id
            WHERE rr.id=?
            """,
            (release_id,),
        ).fetchall()
        if x["app_id"] != x["单应用"] or x["env_id"] != x["单环境"] or x["version"] == x["单目标"]
    )
    # 审计重放：库内仍是回滚中/非已回滚，却已经写下“回滚收敛完成”，即假干净回执
    converged_audits = conn.execute(
        """
        SELECT COUNT(*) AS c FROM audit_events
        WHERE kind='回滚' AND ref_id=? AND action LIKE '%回滚收敛完成%'
        """,
        (release_id,),
    ).fetchone()["c"]
    replay_conflict = converged_audits if r["status"] != "已回滚" else 0
    return {
        "发版单编号": release_id,
        "回退目标版本": r["rollback_target_version"],
        "状态": r["status"],
        "残留旁读": residual_rows,
        "在跑旧版本数": len(rows),
        "退役标缺位": missing_retire,
        "批次对照一致": mismatch == 0,
        "审计重放矛盾": replay_conflict,
        # 物理缺口（在跑旧版本/残留旁读/退役标/批次对照）决定能否收敛；
        # 审计重放作为独立露口，阻止“干净宣称”，但不永久卡死物理已清的收敛。
        "缺口": max(len(rows), residual_rows, missing_retire, mismatch),
        # 干净宣称必须四源同核：已回滚 ∧ 无物理缺口 ∧ 审计重放无矛盾
        "干净可宣称": r["status"] == "已回滚"
        and max(len(rows), residual_rows, missing_retire, mismatch) == 0
        and replay_conflict == 0,
    }


def evaluate_rollback_convergence(conn, release_id: int) -> bool:
    """
    回退再收敛（严格收敛臂）：实例册中该环境下已不再有任何属于本单的非回退目标版本
    在跑行，且审计无假干净回执时，才在同一写事务内把发版单置为已回滚并翻转名册。
    已回滚 ⇔ 旧版本实例清零 ⇔ 名册为回退目标版本，三者恒同时成立。
    仅残留标清零但旧实例仍在跑（退役标缺位/残留标被抹）不得收敛。
    返回是否发生真实收敛翻转。
    """
    gap = rollback_gap(conn, release_id)
    if gap["缺口"] > 0:
        return False
    r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    # 只有 回滚中->已回滚 的真实翻转才返回 True；早已回滚不算新收敛，避免重复审计
    if not r or r["status"] != "回滚中":
        return False
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 写退役标：批次正式行跟着收敛一起落，退役标与残留清零同命运
    conn.execute(
        """
        UPDATE releases SET status='已回滚', rolled_back_at=?, retired_at=?,
               retired_version=rollback_target_version
        WHERE id=?
        """,
        (now, now, release_id),
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
    if "retired_at" not in release_cols:
        conn.execute("ALTER TABLE releases ADD COLUMN retired_at TEXT")
    if "retired_version" not in release_cols:
        conn.execute(
            "ALTER TABLE releases ADD COLUMN retired_version TEXT NOT NULL DEFAULT ''"
        )
    instance_cols = {r["name"] for r in conn.execute("PRAGMA table_info(instances)").fetchall()}
    if "residual_release_id" not in instance_cols:
        conn.execute("ALTER TABLE instances ADD COLUMN residual_release_id INTEGER")
    if "retired_by_release_id" not in instance_cols:
        conn.execute("ALTER TABLE instances ADD COLUMN retired_by_release_id INTEGER")
    # 索引在补列之后统一建，新库旧库均幂等
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_instances_residual ON instances(residual_release_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_instances_retired ON instances(retired_by_release_id)"
    )


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
