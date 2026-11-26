"""
假干净专项夹具：回退页/实例册/批次对照/总览/审计五面同核。

对应事故复现路径：
1) 回退写口返回成功后实例仍跑；2) 残留清了但退役标缺；3) 审计有“收敛完成”而总览不加；
4) 回滚与晋升交错半截。
所有断言只认「残留旁读 ∪ 退役标 ∪ 批次对照 ∪ 审计重放」四源，改干净提示不算收口。
"""
from __future__ import annotations

import sqlite3

from conftest import find_instance_rows, insert_instance, seed_release_to_prod


def _fetch(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _gap(client, rid):
    return client.get(f"/api/发版单/{rid}/缺口").json()


def test_回退写口重放不得把回滚中洗回已放行(client):
    """事故主路径：重复按回退，响应说成功，库里却被抹成已放行且残留仍挂账。"""
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    first = client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"}).json()
    assert first["状态"] == "回滚中" and first["残留数"] == 1

    replay = client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"}).json()
    # 回执不得伪造成功标记
    assert "回执" not in replay and "surface_ok" not in replay
    # 库内状态必须仍是回滚中，残留仍挂账
    db_row = client.get(f"/api/发版单/{rid}").json()
    assert db_row["状态"] == "回滚中"
    assert db_row["残留数"] == 1
    assert _gap(client, rid)["缺口"] == 1


def test_残留清了但旧实例仍跑_不得翻已回滚(client):
    """事故路径：残留标被清、退役标缺位，旧实例仍在跑 —— 干净提示先行亮起即假干净。"""
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    assert client.get(f"/api/发版单/{rid}").json()["状态"] == "回滚中"

    # 只清残留标，不杀旧实例（模拟“残留清零、退役标缺”的半截）
    conn = sqlite3.connect(client.db_path)
    conn.execute(
        "UPDATE instances SET residual_release_id=NULL, retired_by_release_id=NULL "
        "WHERE app_id=? AND version='2.0.0'",
        (app_id,),
    )
    conn.commit()
    conn.close()

    replay = client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"}).json()
    assert replay["状态"] == "回滚中"
    gap = _gap(client, rid)
    assert gap["残留旁读"] == 0            # 残留旁读归零
    assert gap["在跑旧版本数"] == 1        # 但实例仍在跑
    assert gap["退役标缺位"] == 1          # 退役标缺位
    assert gap["缺口"] == 1
    assert gap["干净可宣称"] is False
    # 五面同语：明确回收敛都不许出现
    assert client.get("/api/对账").json()["发版统计"]["已回滚"] == 0
    assert client.get("/api/总览").json()["残留露口"]["已回滚仍有缺口单数"] == 0


def test_已回滚却仍有旧实例_对账与总览必须同屏露缺口(client):
    """直接构造“已回滚 + 旧实例在跑”的伪造库态，五面必须同时判定假干净。"""
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    # 伪造：库里直接把单子改成已回滚，旧实例依旧在跑（残留标也在）
    conn = sqlite3.connect(client.db_path)
    conn.execute(
        "UPDATE releases SET status='已回滚', rollback_target_version='1.0.0' WHERE id=?",
        (rid,),
    )
    conn.commit()
    conn.close()

    gap = _gap(client, rid)
    assert gap["缺口"] >= 1
    assert gap["干净可宣称"] is False

    rec = client.get("/api/对账").json()
    fake = [g for g in rec["回滚缺口桶"] if g["发版单编号"] == rid][0]
    assert fake["发版单状态"] == "已回滚" and fake["缺口"] >= 1
    assert fake["干净可宣称"] is False
    assert rec["假干净单数"] == 1
    assert rec["已回滚仍有残留单数"] == 1

    ov = client.get("/api/总览").json()
    assert ov["残留露口"]["已回滚仍有缺口单数"] == 1
    assert ov["残留露口"]["缺口实例数"] >= 1

    # 审计重放：不得出现与库态矛盾的“收敛完成”回执（伪造前没有写过）
    rid_events = [
        e for e in client.get("/api/审计").json() if e["关联编号"] == rid
    ]
    assert not any("收敛完成" in e["动作"] for e in rid_events)


def test_审计假干净回执被缺口暴露(client):
    """先写“回滚收敛完成”事件但库里不收敛，重放必须揭穿。"""
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    conn = sqlite3.connect(client.db_path)
    # 直接伪造审计回执（不走收敛写口），模拟“审计有、总览不加”的半截
    conn.execute(
        "INSERT INTO audit_events(kind, ref_id, action, created_at, note) "
        "VALUES ('回滚', ?, '回滚收敛完成', 'fixture', '假干净')",
        (rid,),
    )
    conn.commit()
    conn.close()
    gap = _gap(client, rid)
    assert gap["状态"] == "回滚中"
    assert gap["审计重放矛盾"] == 1
    rec = client.get("/api/对账").json()
    fake = [g for g in rec["回滚缺口桶"] if g["发版单编号"] == rid][0]
    assert fake["审计重放矛盾"] == 1


def test_无缺口才允许收敛_退役标与残留清零同屏可核(client):
    """清干净套收口：缺口归零的同事务里，退役标与残留清零同时可核。"""
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    assert client.get(f"/api/发版单/{rid}/残留").json()["残留行"][0]["退役标"] == "有"

    r = client.post(f"/api/发版单/{rid}/清理残留").json()
    assert r["清理残留数"] == 1
    assert r["剩余残留数"] == 0
    assert r["缺口"]["缺口"] == 0
    assert r["发版单"]["状态"] == "已回滚"
    # 退役标随批次正式行同落；产环境只应剩回退目标版本的在册行
    detail = client.get(f"/api/发版单/{rid}").json()
    assert detail["退役版本"] == "1.0.0" and detail["退役时间"]
    prod_rows = [x for x in detail["实例"] if x["环境"] == "产"]
    assert [x["版本"] for x in prod_rows] == ["1.0.0"]
    assert all(x["退役标"] == "缺" for x in prod_rows)  # 目标版本行不挂退役标
    # 三源计数一致
    rec = client.get("/api/对账").json()
    ov = client.get("/api/总览").json()
    rel = client.get("/api/发版单").json()
    assert rec["发版统计"]["已回滚"] == ov["分布"]["已回滚"] == \
        sum(1 for x in rel if x["状态"] == "已回滚") == 1
    assert rec["假干净单数"] == 0 and ov["残留露口"]["已回滚仍有缺口单数"] == 0


def test_退役标缺位时单行清理仍可按批次对照删除(client):
    """残留标被抹、退役标缺位，但实例仍在跑：清理入口须认得出并删除。"""
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    old = find_instance_rows(client, "结算网关", "产", "2.0.0")[0]
    # 抹掉退役标，仅靠残留标 + 批次对照认人
    conn = sqlite3.connect(client.db_path)
    conn.execute(
        "UPDATE instances SET retired_by_release_id=NULL WHERE id=?", (old["编号"],)
    )
    conn.commit()
    conn.close()
    assert _gap(client, rid)["退役标缺位"] == 1
    # 单行清理：认出该行仍属本单缺口
    r = client.post(f"/api/实例/{old['编号']}/清理残留")
    assert r.status_code == 200
    assert r.json()["发版单"]["状态"] == "已回滚"


def test_晋升旁路半截_跨环境晋升不得洗白他环回滚残留(client):
    """回滚中时同应用另一单在别的环境晋升：只动目标环境，不得抹掉产的回滚残留/退役标。"""
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    before = _gap(client, rid)
    assert before["缺口"] == 1

    # 同应用第二张单在测环境走晋升旁路（只动测，不碰产）
    r2 = client.post(
        "/api/发版单",
        json={"应用编号": app_id, "目标版本": "3.0.0", "制品摘要": "sha256:x"},
    ).json()
    client.post(f"/api/发版单/{r2['编号']}/流水线", json={"流水线": "绿"})
    client.post(f"/api/发版单/{r2['编号']}/入闸")
    client.post(f"/api/发版单/{r2['编号']}/晋升", json={"目标环境": "预"})

    after = _gap(client, rid)
    assert after["状态"] == "回滚中"
    assert after["残留旁读"] == 1 and after["退役标缺位"] == 0
    assert after["缺口"] == 1
    assert after["干净可宣称"] is False

    # 半截必须收束：回滚中的单子不得被晋升写口接受
    blocked = client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "产"})
    assert blocked.status_code == 400


def test_健康探针干扰不得抹残留亦不得抹缺口(client):
    """探针改灯不碰残留身份、r 不碰缺口；反复改灯后缺口数恒定。"""
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    resid = find_instance_rows(client, "结算网关", "产", "2.0.0")[0]
    before = _gap(client, rid)
    assert before["缺口"] == 1
    for light in ("探针红", "探针绿", "探针红"):
        client.post(f"/api/实例/{resid['编号']}/健康", json={"健康": light})
    after = _gap(client, rid)
    assert after["缺口"] == before["缺口"] == 1
    assert after["残留旁读"] == 1 and after["退役标缺位"] == 0
    assert client.get(f"/api/发版单/{rid}").json()["状态"] == "回滚中"


def test_实例仍跑与退役缺位两路夹具分测(client):
    """两路缺口分开可复检：实例仍跑(缺口>0) 与 退役标缺位 各自独立。"""
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    # 路 A：实例仍跑
    gap = _gap(client, rid)
    assert gap["在跑旧版本数"] == 1
    assert gap["退役标缺位"] == 0
    # 路 B：人为抹掉退役标，退役标缺位独立露头
    conn = sqlite3.connect(client.db_path)
    conn.execute(
        "UPDATE instances SET retired_by_release_id=NULL WHERE residual_release_id=?",
        (rid,),
    )
    conn.commit()
    conn.close()
    gap2 = _gap(client, rid)
    assert gap2["退役标缺位"] == 1
    assert gap2["在跑旧版本数"] == 1   # 实例仍跑
    assert gap2["缺口"] == 1
    # 补回退役标后该项归零，但实例仍跑仍使缺口为 1
    conn = sqlite3.connect(client.db_path)
    conn.execute(
        "UPDATE instances SET retired_by_release_id=? WHERE residual_release_id=?",
        (rid, rid),
    )
    conn.commit()
    conn.close()
    gap3 = _gap(client, rid)
    assert gap3["退役标缺位"] == 0
    assert gap3["在跑旧版本数"] == 1
    assert gap3["缺口"] == 1


def test_缺口三面同值_回滚页_对账_总览_发版详情(client):
    """同一单的四源缺口在回滚页/对账/总览/发版详情逐面同值，禁止各说各话。"""
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})

    page = _gap(client, rid)                      # 回滚页
    rec = [g for g in client.get("/api/对账").json()["回滚缺口桶"]
           if g["发版单编号"] == rid][0]          # 对账
    ov = [g for g in client.get("/api/总览").json()["残留露口"]["缺口明细"]
          if g["发版单编号"] == rid][0]           # 总览
    detail = client.get(f"/api/发版单/{rid}").json()["回滚缺口"]  # 发版详情

    for other in (rec, ov, detail):
        assert other["残留旁读"] == page["残留旁读"]
        assert other["在跑旧版本数"] == page["在跑旧版本数"]
        assert other["退役标缺位"] == page["退役标缺位"]
        assert other["批次对照一致"] == page["批次对照一致"]
        assert other["审计重放矛盾"] == page["审计重放矛盾"]
        assert other["缺口"] == page["缺口"]
    assert detail["干净可宣称"] == page["干净可宣称"] is False


def test_审计面逐条重放校验_假回执被标假干净(client):
    """审计面不能只列事件：收敛完成回执必须逐条回库重放，矛盾者标假干净。"""
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    conn = sqlite3.connect(client.db_path)
    conn.execute(
        "INSERT INTO audit_events(kind, ref_id, action, created_at, note) "
        "VALUES ('回滚', ?, '回滚收敛完成', 'fixture', '假干净')",
        (rid,),
    )
    conn.commit()
    conn.close()
    rows = [e for e in client.get("/api/审计").json() if e["关联编号"] == rid
            and "收敛完成" in (e["动作"] or "")]
    assert rows and rows[0]["重放校验"] == "假干净"
    assert "回滚中" in rows[0]["重放说明"]
