from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list, rollback_gap

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
        # 回滚缺口桶：不只认残留标，残留旁读 / 退役标 / 批次对照 / 审计重放四源同核。
        # 已回滚却仍有缺口的单子（假干净）在这里显式露头，不允许被任何面板抹平。
        gap_rows_out = []
        for rrow in conn.execute(
            "SELECT id, serial, status FROM releases "
            "WHERE status IN ('回滚中','已回滚') ORDER BY id"
        ).fetchall():
            gap = rollback_gap(conn, rrow["id"])
            gap_rows_out.append({
                "发版单编号": rrow["id"],
                "发版单号": rrow["serial"],
                "发版单状态": rrow["status"],
                "残留旁读": gap["残留旁读"],
                "在跑旧版本数": gap["在跑旧版本数"],
                "退役标缺位": gap["退役标缺位"],
                "批次对照一致": gap["批次对照一致"],
                "审计重放矛盾": gap["审计重放矛盾"],
                "缺口": gap["缺口"],
                "干净可宣称": gap["干净可宣称"],
            })
        fake_clean = [g for g in gap_rows_out if not g["干净可宣称"] and g["发版单状态"] == "已回滚"]
        promote_count = conn.execute("SELECT COUNT(*) AS c FROM promote_events").fetchone()["c"]
        return {
            "发版统计": release_stats,
            "晋升事件数": promote_count,
            "名册与实例": version_vs_instance,
            "回退残留桶": residual_bucket,
            "残留实例总数": residual_instance_total,
            "已回滚仍有残留单数": rolled_back_with_residual,
            "回滚缺口桶": gap_rows_out,
            "假干净单数": len(fake_clean),
            "完成口径": "已回滚当且仅当残留旁读∪退役标∪批次对照∪审计重放四源皆无缺口；"
                        "回滚中即明示残留账，不计已回滚",
        }

