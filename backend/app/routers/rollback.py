from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import (
    db,
    row_to_dict,
    rows_to_list,
    write_tx,
    residual_count,
    rollback_gap,
    evaluate_rollback_convergence,
)
from app.services.common import (
    active_rollback_window,
    audit,
    get_release,
    now_str,
    upsert_instance,
)

router = APIRouter(tags=["回滚"])


class RollbackIn(BaseModel):
    目标版本: str = Field(min_length=1)
    备注: str = ""


def _assert_rollback_window(conn, r) -> None:
    if not r["current_env_id"]:
        raise HTTPException(400, "无当前环境，无法回滚")
    win = active_rollback_window(conn, r["current_env_id"])
    if not win:
        raise HTTPException(400, "回滚窗已关，禁止回滚")


@router.post("/发版单/{release_id}/回滚")
def rollback(release_id: int, body: RollbackIn):
    """
    回退写口（严格收敛臂）：
    启动时发版单进入回滚中并挂残留账；残留清零的同一事务内才置已回滚并翻转名册。
    同发版单同目标版本幂等可重放；不同目标版本在回滚中/已回滚后一律拒绝。
    """
    with write_tx() as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")

        # 幂等重放：已经在回滚流程里，目标版本一致即按真实缺口重放收敛判定，结局与时序无关
        if r["status"] in ("回滚中", "已回滚"):
            if r["rollback_target_version"] != body.目标版本:
                raise HTTPException(400, "回退目标版本不可变更，请沿用原回退目标")
            if r["status"] == "回滚中":
                if evaluate_rollback_convergence(conn, release_id):
                    audit(conn, "回滚", release_id, "回滚收敛完成", f"目标{body.目标版本}")
            # 回执与库内状态同源：绝不改写发版单、绝不伪造表面成功
            return row_to_dict(get_release(conn, release_id))

        if r["status"] not in ("已放行", "晋升中"):
            raise HTTPException(400, "当前状态不可回滚")
        _assert_rollback_window(conn, r)

        now = now_str()
        # 1) 发版单进入回滚中，记录回退目标；名册此刻不切
        conn.execute(
            """
            UPDATE releases
            SET status='回滚中', rollback_target_version=?, rolled_back_at=NULL,
                retired_at=NULL, retired_version=''
            WHERE id=?
            """,
            (body.目标版本, release_id),
        )
        # 2) 同应用同环境、版本不等于回退目标的在册实例全部挂残留账并落退役标
        #    （退役标是批次粒度：谁把这批旧包退役的，逐行记在同一发版单名下）
        conn.execute(
            """
            UPDATE instances
            SET residual_release_id=?, retired_by_release_id=?, updated_at=?
            WHERE app_id=? AND env_id=? AND version!=?
            """,
            (release_id, release_id, now, r["app_id"], r["current_env_id"], body.目标版本),
        )
        # 3) 目标版本实例确保在册（绿灯仅代表探针，不代表收敛），并清掉其残留/退役身份
        upsert_instance(
            conn, r["app_id"], r["current_env_id"], body.目标版本, "探针绿", release_id
        )
        conn.execute(
            """
            UPDATE instances SET residual_release_id=NULL, retired_by_release_id=NULL,
                   updated_at=?
            WHERE app_id=? AND env_id=? AND version=?
            """,
            (now, r["app_id"], r["current_env_id"], body.目标版本),
        )
        audit(conn, "回滚", release_id, f"回滚启动到{body.目标版本}", body.备注)

        # 4) 启动即无残留（旧包已不在册）：同事务收敛，名册翻转、置已回滚
        if evaluate_rollback_convergence(conn, release_id):
            audit(conn, "回滚", release_id, "回滚收敛完成", f"目标{body.目标版本}")
        return row_to_dict(get_release(conn, release_id))


@router.post("/发版单/{release_id}/清理残留")
def purge_residuals(release_id: int):
    """
    按发版单批量清理残留（清理入口之一）。
    仅删除挂着该单残留标的实例行；清理后在同事务立即再收敛。
    残留清零 ≠ 干净：只要还有本单非目标版本实例在跑，收敛判定仍然拒绝翻已回滚。
    """
    with write_tx() as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        before = residual_count(conn, release_id)
        # 残留旁读与退役标同命运：二者任一还挂着本单，都算本单未清的实例行，一并删除。
        # 只清残留标、留下退役标或旧实例仍在跑，都不得让本单翻已回滚。
        stale = conn.execute(
            """
            DELETE FROM instances
            WHERE residual_release_id=? OR retired_by_release_id=?
            """,
            (release_id, release_id),
        ).rowcount
        if before:
            audit(conn, "回滚", release_id, "残留清理", f"批量清理{before}个残留实例")
        if stale > before:
            audit(conn, "回滚", release_id, "残留清理",
                  f"退役标缺口补清{stale - before}个实例")
        converged = evaluate_rollback_convergence(conn, release_id)
        if converged:
            audit(
                conn,
                "回滚",
                release_id,
                "回滚收敛完成",
                f"目标{r['rollback_target_version']}",
            )
        return {
            "编号": release_id,
            "清理残留数": before,
            "剩余残留数": residual_count(conn, release_id),
            "缺口": rollback_gap(conn, release_id),
            "发版单": row_to_dict(get_release(conn, release_id)),
        }


@router.get("/发版单/{release_id}/缺口")
def release_gap(release_id: int):
    """回滚缺口三源同核 + 审计重放旁读：残留旁读 / 退役标 / 批次对照 / 审计重放。"""
    with db() as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        return rollback_gap(conn, release_id)


@router.get("/发版单/{release_id}/残留")
def list_residuals(release_id: int):
    with db() as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        rows = conn.execute(
            """
            SELECT i.id AS 编号, a.name AS 应用, e.code AS 环境,
                   i.version AS 版本, i.health AS 健康,
                   i.residual_release_id AS 残留归属发版单,
                   i.retired_by_release_id AS 退役标记,
                   CASE WHEN i.retired_by_release_id IS NULL THEN '缺' ELSE '有' END AS 退役标,
                   i.updated_at AS 更新时间
            FROM instances i
            JOIN apps a ON a.id=i.app_id
            JOIN environments e ON e.id=i.env_id
            WHERE i.residual_release_id=?
            ORDER BY i.id
            """,
            (release_id,),
        ).fetchall()
        return {
            "编号": release_id,
            "回退目标版本": r["rollback_target_version"],
            "状态": r["status"],
            "残留数": residual_count(conn, release_id),
            "缺口": rollback_gap(conn, release_id),
            "残留行": rows_to_list(rows),
        }
