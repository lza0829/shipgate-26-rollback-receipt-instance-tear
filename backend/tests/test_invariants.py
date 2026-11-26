"""跨面不变量：八面认同一个完成定义，任何视角都不得自相矛盾。"""
from __future__ import annotations

import sqlite3

from conftest import find_instance_rows, insert_instance, seed_release_to_prod


def _db(conn_sql, db_path, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(conn_sql, params).fetchall()
    finally:
        conn.close()


def test_已回滚计数三源一致(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    residual = find_instance_rows(client, "结算网关", "产", "2.0.0")[0]

    mid_rec = client.get("/api/对账").json()
    mid_ov = client.get("/api/总览").json()
    mid_rel = client.get("/api/发版单").json()
    assert mid_rec["发版统计"]["已回滚"] == mid_ov["分布"]["已回滚"]
    assert mid_rec["发版统计"]["已回滚"] == sum(1 for r in mid_rel if r["状态"] == "已回滚")
    assert mid_rec["发版统计"]["已回滚"] == 0

    client.post(f"/api/实例/{residual['编号']}/清理残留")
    rec = client.get("/api/对账").json()
    ov = client.get("/api/总览").json()
    rel = client.get("/api/发版单").json()
    assert rec["发版统计"]["已回滚"] == 1
    assert ov["分布"]["已回滚"] == 1
    assert sum(1 for r in rel if r["状态"] == "已回滚") == 1


def test_残留总数实例册与总览对账一致(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    insert_instance(client.db_path, app_id, "产", "1.9.0", release_id=rid)
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})

    db_count = _db(
        "SELECT COUNT(*) FROM instances WHERE residual_release_id IS NOT NULL",
        client.db_path,
    )[0][0]
    rec = client.get("/api/对账").json()
    ov = client.get("/api/总览").json()
    assert db_count == rec["残留实例总数"] == ov["残留露口"]["残留实例数"] == 2
    assert sum(b["残留实例数"] for b in rec["回退残留桶"]) == 2


def test_不存在已回滚且有残留的单子(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    # 直接核库守卫
    bad = _db(
        """
        SELECT COUNT(*) FROM releases r
        WHERE r.status='已回滚'
          AND EXISTS (SELECT 1 FROM instances i WHERE i.residual_release_id=r.id)
        """,
        client.db_path,
    )[0][0]
    assert bad == 0
    assert client.get("/api/对账").json()["已回滚仍有残留单数"] == 0


def test_回滚中名册不得提前等于回退目标(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    rows = _db(
        """
        SELECT v.version, r.rollback_target_version, r.status
        FROM releases r JOIN app_env_versions v
          ON v.app_id=r.app_id AND v.env_id=r.current_env_id
        WHERE r.id=?
        """,
        client.db_path,
        (rid,),
    )[0]
    assert rows[2] == "回滚中"
    assert rows[0] != rows[1]   # 名册版本 != 回退目标版本
    assert rows[0] == "2.0.0"


def test_残留行版本必不等于回退目标且同应用同环境(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    bad = _db(
        """
        SELECT COUNT(*) FROM instances i JOIN releases r ON r.id=i.residual_release_id
        WHERE i.version=r.rollback_target_version
           OR i.app_id!=r.app_id OR i.env_id!=r.current_env_id
        """,
        client.db_path,
    )[0][0]
    assert bad == 0


def test_审计完整记录启动清理收敛三类事件(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0", "备注": "n"})
    residual = find_instance_rows(client, "结算网关", "产", "2.0.0")[0]
    client.post(f"/api/实例/{residual['编号']}/清理残留")
    events = [e for e in client.get("/api/审计").json() if e["关联编号"] == rid]
    actions = "|".join(e["动作"] for e in events)
    assert "回滚启动" in actions
    assert "残留清理" in actions
    assert "回滚收敛完成" in actions


def test_回滚窗关闭时禁止回退(client):
    rid, _ = seed_release_to_prod(client)
    client.post("/api/回滚窗/切换", json={"环境": "产", "状态": "窗关"})
    resp = client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    assert resp.status_code == 400
    assert client.get(f"/api/发版单/{rid}").json()["状态"] == "已放行"


def test_正常晋升放行流程不被残留逻辑误伤(client):
    rid, _ = seed_release_to_prod(client, target_version="3.1.0")
    rel = client.get(f"/api/发版单/{rid}").json()
    assert rel["状态"] == "已放行"
    assert rel["残留数"] == 0
    rec = client.get("/api/对账").json()
    assert rec["残留实例总数"] == 0
    assert rec["回退残留桶"] == []
    ov = client.get("/api/总览").json()
    assert ov["残留露口"]["残留实例数"] == 0
