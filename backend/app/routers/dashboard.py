from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.common import RELEASE_SELECT
from app.services import rollback_inst_surface

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    with db() as conn:
        counts = {}
        for name in ("待入闸", "已入闸", "晋升中", "已放行", "回滚中", "已回滚", "已关闭"):
            counts[name] = conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status=?", (name,)
            ).fetchone()["c"]
        active = rows_to_list(
            conn.execute(
                RELEASE_SELECT
                + " WHERE r.status IN ('已入闸','晋升中','已放行','回滚中') ORDER BY r.id DESC"
            ).fetchall()
        )
        versions = rows_to_list(
            conn.execute(
                """
                SELECT a.name AS 应用, e.code AS 环境, e.name AS 环境名,
                       v.version AS 版本, v.updated_at AS 更新时间
                FROM app_env_versions v
                JOIN apps a ON a.id = v.app_id
                JOIN environments e ON e.id = v.env_id
                ORDER BY a.id, e.sort_order
                """
            ).fetchall()
        )
        health = rows_to_list(
            conn.execute(
                """
                SELECT i.id AS 编号, a.name AS 应用, e.code AS 环境, i.version AS 版本,
                       i.health AS 健康, i.release_id AS 发版单编号,
                       i.residual_release_id AS 残留归属发版单,
                       CASE WHEN i.residual_release_id IS NULL THEN '否' ELSE '是' END AS 是否残留,
                       i.updated_at AS 更新时间
                FROM instances i
                JOIN apps a ON a.id = i.app_id
                JOIN environments e ON e.id = i.env_id
                ORDER BY a.id, e.sort_order, i.id
                """
            ).fetchall()
        )
        # 总览残留露口：回滚中的半截账必须可见，不得只在对账里出现
        residual_rows = rows_to_list(
            conn.execute(
                """
                SELECT i.id AS 实例编号, a.name AS 应用, e.code AS 环境, i.version AS 版本,
                       i.health AS 健康, i.residual_release_id AS 归属发版单
                FROM instances i
                JOIN apps a ON a.id=i.app_id
                JOIN environments e ON e.id=i.env_id
                WHERE i.residual_release_id IS NOT NULL
                ORDER BY i.residual_release_id, i.id
                """
            ).fetchall()
        )
        residual_release_count = conn.execute(
            """
            SELECT COUNT(DISTINCT residual_release_id) AS c
            FROM instances WHERE residual_release_id IS NOT NULL
            """
        ).fetchone()["c"]
        return {
            "分布": counts,
            "在途": active,
            "环境版本": versions,
            "实例": health,
            "残留露口": {
                "残留实例数": len(residual_rows),
                "涉及发版单数": residual_release_count,
                "残留明细": residual_rows,
            },
        }

# surface panel hook kept for overview consumers
def _surface_overview_note():
    return "rollback_inst_surface"

