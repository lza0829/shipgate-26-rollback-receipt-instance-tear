from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import (
    db,
    row_to_dict,
    rows_to_list,
    write_tx,
    gap_rows,
    evaluate_rollback_convergence,
)
from app.services.common import audit, get_release, now_str

router = APIRouter(tags=["健康"])


class HealthIn(BaseModel):
    健康: str


@router.get("/实例")
def list_instances():
    with db() as conn:
        return rows_to_list(
            conn.execute(
                """
                SELECT i.id AS 编号, a.name AS 应用, e.code AS 环境, e.name AS 环境名,
                       i.version AS 版本, i.health AS 健康, i.release_id AS 发版单编号,
                       i.residual_release_id AS 残留归属发版单,
                       i.retired_by_release_id AS 退役标记,
                       CASE WHEN i.residual_release_id IS NULL THEN '否' ELSE '是' END AS 是否残留,
                       CASE WHEN i.retired_by_release_id IS NULL THEN '缺' ELSE '有' END AS 退役标,
                       i.updated_at AS 更新时间
                FROM instances i
                JOIN apps a ON a.id = i.app_id
                JOIN environments e ON e.id = i.env_id
                ORDER BY a.id, e.sort_order, i.id
                """
            ).fetchall()
        )


@router.post("/实例/{instance_id}/健康")
def set_health(instance_id: int, body: HealthIn):
    """
    探针写口：只改健康灯，绝不改残留身份。
    走 BEGIN IMMEDIATE，与回退写口互斥，并发结局可重放。
    """
    if body.健康 not in ("探针绿", "探针红"):
        raise HTTPException(400, "健康状态非法")
    with write_tx() as conn:
        row = conn.execute("SELECT * FROM instances WHERE id=?", (instance_id,)).fetchone()
        if not row:
            raise HTTPException(404, "实例不存在")
        conn.execute(
            "UPDATE instances SET health=?, updated_at=? WHERE id=?",
            (body.健康, now_str(), instance_id),
        )
        audit(conn, "探针", instance_id, body.健康,
              f"残留标{row['residual_release_id'] or '无'}")
        return {
            "编号": instance_id,
            "健康": body.健康,
            "是否残留": "是" if row["residual_release_id"] else "否",
        }


@router.post("/实例/{instance_id}/清理残留")
def purge_one(instance_id: int):
    """
    单行残留清理入口：只允许清理“本单回退缺口行”。
    缺口行 = 该实例仍在跑非回退目标版本；即使残留标被抹、退役标缺位，
    与它归属发版单核对后仍必须从实例册删除，才可能宣称收敛。
    探针不能替代本入口。
    """
    with write_tx() as conn:
        row = conn.execute("SELECT * FROM instances WHERE id=?", (instance_id,)).fetchone()
        if not row:
            raise HTTPException(404, "实例不存在")
        rid = row["residual_release_id"] or row["retired_by_release_id"]
        if not rid:
            raise HTTPException(400, "该实例不是回退残留，禁止通过残留清理删除")
        # 批次对照：该行必须确实是本单的缺口行（同应用同环境、非回退目标版本）
        is_gap = any(x["id"] == instance_id for x in gap_rows(conn, rid))
        if not is_gap:
            raise HTTPException(400, "该实例已与发版单批次对照不符，禁止清理")
        conn.execute("DELETE FROM instances WHERE id=?", (instance_id,))
        audit(conn, "回滚", rid, "残留清理", f"清理实例{instance_id}（版本{row['version']}）")
        converged = evaluate_rollback_convergence(conn, rid)
        if converged:
            r = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
            audit(conn, "回滚", rid, "回滚收敛完成", f"目标{r['rollback_target_version']}")
        return {
            "编号": instance_id,
            "归属发版单": rid,
            "发版单": row_to_dict(get_release(conn, rid)),
        }

