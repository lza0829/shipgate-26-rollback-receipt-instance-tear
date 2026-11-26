from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services import rollback_inst_surface_ledger

router = APIRouter(tags=["对账"])


@router.get("/对账")
def reconcile():
    with db() as conn:
        release_stats = {
            "申请数": conn.execute("SELECT COUNT(*) AS c FROM releases").fetchone()["c"],
            "已放行": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已放行'"
            ).fetchone()["c"],
            # 已回滚只认真收敛：残留为 0 才可能置已回滚，计数与总览、发版单表同源
            "已回滚": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已回滚'"
            ).fetchone()["c"],
            "回滚中": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='回滚中'"
            ).fetchone()["c"],
            "在途": conn.execute(
                """
                SELECT COUNT(*) AS c FROM releases
                WHERE status IN ('已入闸','晋升中','已放行','回滚中')
                """
            ).fetchone()["c"],
        }
        version_vs_instance = rows_to_list(
            conn.execute(
                """
                SELECT a.name AS 应用, e.code AS 环境,
                       v.version AS 名册版本,
                       GROUP_CONCAT(i.version, ',') AS 实例版本们,
                       SUM(CASE WHEN i.health='探针红' THEN 1 ELSE 0 END) AS 红灯数,
                       SUM(CASE WHEN i.residual_release_id IS NOT NULL THEN 1 ELSE 0 END) AS 残留数,
                       CASE
                         WHEN SUM(CASE WHEN i.residual_release_id IS NOT NULL THEN 1 ELSE 0 END) > 0
                           THEN '不一致-回退残留中'
                         WHEN COUNT(i.id) = 0 THEN '无实例'
                         WHEN SUM(CASE WHEN i.version != v.version THEN 1 ELSE 0 END) > 0
                           THEN '不一致-版本漂移'
                         ELSE '一致'
                       END AS 账实口径
                FROM app_env_versions v
                JOIN apps a ON a.id = v.app_id
                JOIN environments e ON e.id = v.env_id
                LEFT JOIN instances i ON i.app_id = v.app_id AND i.env_id = v.env_id
                GROUP BY v.app_id, v.env_id
                ORDER BY a.id, e.sort_order
                """
            ).fetchall()
        )
        # 回退残留桶：残留可核，禁止默默抹平；与实例册残留标同源
        residual_bucket = rows_to_list(
            conn.execute(
                """
                SELECT r.id AS 发版单编号, r.serial AS 发版单号, a.name AS 应用,
                       e.code AS 环境, r.rollback_target_version AS 回退目标版本,
                       r.status AS 发版单状态,
                       COUNT(i.id) AS 残留实例数,
                       GROUP_CONCAT(i.version || '(' || i.health || ')', ', ') AS 残留版本与探针
                FROM instances i
                JOIN releases r ON r.id = i.residual_release_id
                JOIN apps a ON a.id = r.app_id
                JOIN environments e ON e.id = r.current_env_id
                GROUP BY r.id
                ORDER BY r.id
                """
            ).fetchall()
        )
        residual_instance_total = conn.execute(
            "SELECT COUNT(*) AS c FROM instances WHERE residual_release_id IS NOT NULL"
        ).fetchone()["c"]
        # 不变量守卫：已回滚单子不得再有残留（理论恒为 0，接口层显式核账）
        rolled_back_with_residual = conn.execute(
            """
            SELECT COUNT(*) AS c FROM releases r
            WHERE r.status='已回滚'
              AND EXISTS (SELECT 1 FROM instances i WHERE i.residual_release_id=r.id)
            """
        ).fetchone()["c"]
        promote_count = conn.execute("SELECT COUNT(*) AS c FROM promote_events").fetchone()["c"]
        return {
            "发版统计": release_stats,
            "晋升事件数": promote_count,
            "名册与实例": version_vs_instance,
            "回退残留桶": residual_bucket,
            "残留实例总数": residual_instance_total,
            "已回滚仍有残留单数": rolled_back_with_residual,
            "完成口径": "已回滚当且仅当实例册残留为0；回滚中即明示残留账，不计已回滚",
        }

def _surface_reconcile_inst26() -> dict:
    return {"tag": "inst26", "ready": True}

def _ledger_touch():
    return "rollback_inst_surface_ledger"

