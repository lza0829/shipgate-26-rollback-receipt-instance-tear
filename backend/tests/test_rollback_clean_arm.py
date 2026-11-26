"""夹具套 B：清干净再标成功（收敛事务内名册与状态同时翻转，含幂等重放）。"""
from __future__ import annotations

import threading

from conftest import find_instance_rows, insert_instance, seed_release_to_prod


def test_启动即无残留_同事务直接收敛(client):
    rid, app_id = seed_release_to_prod(client)
    # 模拟旧包已全部退场：册内只保留回退目标版本 1.0.0，删除 2.0.0 行
    import sqlite3

    conn = sqlite3.connect(client.db_path)
    conn.execute("DELETE FROM instances WHERE version='2.0.0'")
    conn.execute(
        """
        INSERT INTO instances(app_id, env_id, version, health, release_id, updated_at)
        SELECT app_id, current_env_id, '1.0.0', '探针绿', NULL, 'fixture'
        FROM releases WHERE id=?
        """,
        (rid,),
    )
    conn.commit()
    conn.close()

    resp = client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    body = resp.json()
    assert body["状态"] == "已回滚"
    assert body["残留数"] == 0
    # 名册已在同一事务切到目标版本
    ov = client.get("/api/总览").json()
    prod_ver = [v for v in ov["环境版本"] if v["环境"] == "产" and v["应用"] == "结算网关"][0]
    assert prod_ver["版本"] == "1.0.0"
    rec = client.get("/api/对账").json()
    assert rec["发版统计"]["已回滚"] == 1
    assert rec["残留实例总数"] == 0
    actions = [e["动作"] for e in client.get("/api/审计").json() if e["关联编号"] == rid]
    assert any("回滚启动" in a for a in actions)
    assert any("收敛完成" in a for a in actions)


def test_单行清理后收敛_状态与名册同时翻转(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    residual = find_instance_rows(client, "结算网关", "产", "2.0.0")[0]

    # 非残留行禁止走清理入口
    target = find_instance_rows(client, "结算网关", "产", "1.0.0")[0]
    forbid = client.post(f"/api/实例/{target['编号']}/清理残留")
    assert forbid.status_code == 400

    r = client.post(f"/api/实例/{residual['编号']}/清理残留")
    assert r.status_code == 200
    assert r.json()["发版单"]["状态"] == "已回滚"
    assert find_instance_rows(client, "结算网关", "产", "2.0.0") == []
    # 名册同步
    rec = client.get("/api/对账").json()
    prod = [x for x in rec["名册与实例"] if x["环境"] == "产"
            and x["应用"] == "结算网关"][0]
    assert prod["名册版本"] == "1.0.0"
    assert prod["账实口径"] == "一致"
    assert rec["发版统计"]["已回滚"] == 1
    assert rec["残留实例总数"] == 0


def test_批量清理入口收敛(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    insert_instance(client.db_path, app_id, "产", "1.9.0", release_id=rid)
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    assert client.get(f"/api/发版单/{rid}").json()["残留数"] == 2

    r = client.post(f"/api/发版单/{rid}/清理残留")
    assert r.status_code == 200
    assert r.json()["清理残留数"] == 2
    assert r.json()["剩余残留数"] == 0
    assert r.json()["发版单"]["状态"] == "已回滚"
    # 重复清理幂等，不产生幻影行
    again = client.post(f"/api/发版单/{rid}/清理残留")
    assert again.json()["清理残留数"] == 0
    assert again.json()["发版单"]["状态"] == "已回滚"


def test_清理一半仍停留回滚中(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    insert_instance(client.db_path, app_id, "产", "1.9.0", release_id=rid)
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    first = find_instance_rows(client, "结算网关", "产", "2.0.0")[0]
    client.post(f"/api/实例/{first['编号']}/清理残留")
    rel = client.get(f"/api/发版单/{rid}").json()
    assert rel["状态"] == "回滚中"
    assert rel["残留数"] == 1
    # 名册仍未切
    rec = client.get("/api/对账").json()
    prod = [x for x in rec["名册与实例"] if x["环境"] == "产"
            and x["应用"] == "结算网关"][0]
    assert prod["名册版本"] == "2.0.0"
    assert rec["发版统计"]["已回滚"] == 0


def test_回退写口幂等重放(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    # 同目标重放：仍回滚中，残留不变，结局与时序无关
    replay = client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    assert replay.json()["状态"] == "回滚中"
    assert replay.json()["残留数"] == 1
    # 清完后再重放：已回滚稳定
    residual = find_instance_rows(client, "结算网关", "产", "2.0.0")[0]
    client.post(f"/api/实例/{residual['编号']}/清理残留")
    replay2 = client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    assert replay2.json()["状态"] == "已回滚"
    assert replay2.json()["残留数"] == 0


def test_并发回退与心跳_结局可重放(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    errors = []

    def rollback():
        try:
            client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
        except Exception as e:  # pragma: no cover - 仅记录
            errors.append(e)

    def heartbeat():
        try:
            residual = find_instance_rows(client, "结算网关", "产", "2.0.0")
            if residual:
                client.post(f"/api/实例/{residual[0]['编号']}/健康",
                            json={"健康": "探针红"})
        except Exception as e:  # pragma: no cover
            errors.append(e)

    t1 = threading.Thread(target=rollback)
    t2 = threading.Thread(target=heartbeat)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert not errors
    rel = client.get(f"/api/发版单/{rid}").json()
    assert rel["状态"] == "回滚中" and rel["残留数"] == 1
    # 无论心跳先到还是后到，残留行必然仍挂账且状态可被对账核到
    rec = client.get("/api/对账").json()
    assert rec["残留实例总数"] == 1
    assert rec["已回滚仍有残留单数"] == 0
