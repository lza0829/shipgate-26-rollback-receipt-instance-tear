from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.config as configmod  # noqa: E402
import app.db as dbmod  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """
    每用例独立临时库。connect() 在调用时才读 app.db.DB_PATH 全局，
    故替换该全局后重新建库播种即可，无需热加载模块。
    """
    db_file = tmp_path / "shipgate.db"
    monkeypatch.setattr(dbmod, "DB_PATH", db_file)
    monkeypatch.setattr(configmod, "DB_PATH", db_file)
    seed_if_empty()
    with TestClient(app) as c:
        c.db_path = str(db_file)
        yield c


def seed_release_to_prod(client, target_version: str = "2.0.0", digest: str = "sha256:abc"):
    """造一张已放行到生产、实例册已挂目标版本的发版单，返回其编号。"""
    import sqlite3

    # 种子为结算网关预置了一张在途发版单，占用并行入闸闸位，先关闭并清空实例册
    conn = sqlite3.connect(client.db_path)
    conn.execute(
        "UPDATE releases SET status='已关闭' WHERE status IN ('已入闸','晋升中','已放行')"
    )
    conn.execute("DELETE FROM instances")
    conn.commit()
    conn.close()

    apps = client.get("/api/应用").json()
    app_id = apps[0]["编号"]
    r = client.post(
        "/api/发版单",
        json={"应用编号": app_id, "目标版本": target_version, "制品摘要": digest},
    ).json()
    rid = r["编号"]
    client.post(f"/api/发版单/{rid}/流水线", json={"流水线": "绿"})
    client.post(f"/api/发版单/{rid}/入闸")
    client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "预"})
    client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "产"})
    return rid, app_id


def insert_instance(db_path: str, app_id: int, env_code: str, version: str,
                    release_id: int | None = None, residual: int | None = None,
                    health: str = "探针绿"):
    """直接在实例册补行，用于构造半截部分切流/多版本并存等夹具状态。"""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        env_id = conn.execute(
            "SELECT id FROM environments WHERE code=?", (env_code,)
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO instances(app_id, env_id, version, health, release_id,
                                  residual_release_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'fixture')
            """,
            (app_id, env_id, version, health, release_id, residual),
        )
        conn.commit()
        return conn.execute("SELECT id FROM instances ORDER BY id DESC LIMIT 1").fetchone()[0]
    finally:
        conn.close()


def find_instance_rows(client, app_name: str, env_code: str, version: str):
    rows = client.get("/api/实例").json()
    return [
        r for r in rows
        if r["应用"] == app_name and r["环境"] == env_code and r["版本"] == version
    ]
