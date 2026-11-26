"""夹具套 A：回退后留旧包（严格收敛臂——不得标已回滚，残留全程可核）。"""
from __future__ import annotations

from conftest import find_instance_rows, insert_instance, seed_release_to_prod


def test_回退后旧包仍在_停在回滚中并挂残留账(client):
    rid, app_id = seed_release_to_prod(client, target_version="2.0.0")
    # 旧包进程仍在：册内同时存在 2.0.0（新）与 1.0.0（旧）
    insert_instance(client.db_path, app_id, "产", "1.0.0", release_id=None)

    resp = client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["状态"] == "回滚中"
    assert body["残留数"] == 1
    assert body["回退目标版本"] == "1.0.0"

    old = find_instance_rows(client, body["应用"], "产", "1.0.0")
    new = find_instance_rows(client, body["应用"], "产", "2.0.0")
    assert len(old) == 1 and old[0]["是否残留"] == "否"          # 目标版本不挂残留
    assert len(new) == 1 and new[0]["残留归属发版单"] == rid      # 新包=残留


def test_探针与残留正交_心跳改灯不抹残留(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    residual = find_instance_rows(client, "结算网关", "产", "2.0.0")[0]
    assert residual["健康"] == "探针绿"   # 残留行可以是绿灯

    r = client.post(f"/api/实例/{residual['编号']}/健康", json={"健康": "探针红"})
    assert r.status_code == 200 and r.json()["是否残留"] == "是"
    again = find_instance_rows(client, "结算网关", "产", "2.0.0")[0]
    assert again["健康"] == "探针红" and again["残留归属发版单"] == rid
    # 再心跳回绿，残留身份依旧
    client.post(f"/api/实例/{residual['编号']}/健康", json={"健康": "探针绿"})
    again = find_instance_rows(client, "结算网关", "产", "2.0.0")[0]
    assert again["健康"] == "探针绿" and again["残留归属发版单"] == rid
    rel = client.get(f"/api/发版单/{rid}").json()
    assert rel["状态"] == "回滚中" and rel["残留数"] == 1


def test_半截部分切流_各面不得提前报成功(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})

    # 对账：不计已回滚，残留桶可核，账实口径标不一致
    rec = client.get("/api/对账").json()
    assert rec["发版统计"]["已回滚"] == 0
    assert rec["发版统计"]["回滚中"] == 1
    assert rec["残留实例总数"] == 1
    assert rec["已回滚仍有残留单数"] == 0
    bucket = {b["发版单编号"]: b for b in rec["回退残留桶"]}
    assert rid in bucket and bucket[rid]["残留实例数"] == 1
    assert "2.0.0" in bucket[rid]["残留版本与探针"]
    ledger = [x for x in rec["名册与实例"] if x["环境"] == "产"
              and x["应用"] == "结算网关"][0]
    assert ledger["账实口径"] == "不一致-回退残留中"
    assert ledger["残留数"] == 1
    # 名册仍是回退前版本，未提前切
    assert ledger["名册版本"] == "2.0.0"

    # 总览：回滚中计数 +1，已回滚 0，残留露口可见
    ov = client.get("/api/总览").json()
    assert ov["分布"]["回滚中"] == 1
    assert ov["分布"]["已回滚"] == 0
    assert ov["残留露口"]["残留实例数"] == 1
    assert ov["残留露口"]["涉及发版单数"] == 1
    assert ov["残留露口"]["残留明细"][0]["归属发版单"] == rid

    # 发版单列表同源
    rel = client.get(f"/api/发版单/{rid}").json()
    assert rel["状态"] == "回滚中" and rel["残留数"] == 1


def test_残留查询口可核(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    detail = client.get(f"/api/发版单/{rid}/残留").json()
    assert detail["状态"] == "回滚中"
    assert detail["残留数"] == 1
    assert detail["残留行"][0]["版本"] == "2.0.0"


def test_审计记录启动但不记录完成(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0", "备注": "事故回退"})
    actions = [e["动作"] for e in client.get("/api/审计").json() if e["关联编号"] == rid]
    assert any("回滚启动" in a for a in actions)
    assert not any("收敛完成" in a for a in actions)


def test_回滚中变更目标版本被拒(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    resp = client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "0.9.0"})
    assert resp.status_code == 400


def test_多个残留实例全部挂账(client):
    rid, app_id = seed_release_to_prod(client)
    insert_instance(client.db_path, app_id, "产", "1.0.0")
    insert_instance(client.db_path, app_id, "产", "1.9.0", release_id=rid)
    resp = client.post(f"/api/发版单/{rid}/回滚", json={"目标版本": "1.0.0"})
    # 2.0.0 与 1.9.0 均非目标版本，全部为残留
    assert resp.json()["残留数"] == 2
    assert client.get("/api/对账").json()["残留实例总数"] == 2
