from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit():
    """
    审计流水 + 事件重放校验：每条“回滚收敛完成”回执都要回库对一次，
    库内状态或缺口与回执不符时标“假干净”，不允许审计面自说自话。
    """
    from app.db import rollback_gap

    with db() as conn:
        rows = rows_to_list(
            conn.execute(
                """
                SELECT id AS 编号, kind AS 类别, ref_id AS 关联编号,
                       action AS 动作, created_at AS 时间, note AS 备注
                FROM audit_events ORDER BY id DESC LIMIT 200
                """
            ).fetchall()
        )
        for row in rows:
            row["重放校验"] = "—"
            row["重放说明"] = ""
            if row["类别"] == "回滚" and "收敛完成" in (row["动作"] or ""):
                rid = row["关联编号"]
                gap = rollback_gap(conn, rid) if rid else None
                if not gap:
                    row["重放校验"] = "无单"
                    row["重放说明"] = "回执指向的发版单不存在"
                elif gap["状态"] != "已回滚":
                    row["重放校验"] = "假干净"
                    row["重放说明"] = f"库内状态仍为{gap['状态']}"
                elif gap["缺口"] > 0:
                    row["重放校验"] = "假干净"
                    row["重放说明"] = (
                        f"缺口{gap['缺口']}（在跑旧版本{gap['在跑旧版本数']}/"
                        f"退役标缺位{gap['退役标缺位']}）"
                    )
                else:
                    row["重放校验"] = "一致"
                    row["重放说明"] = "四源同核通过"
        return rows
