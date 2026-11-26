# 发版闸（Shipgate）

骨架：发版申请入闸 → 环境晋升裁决 → 放行/回滚窗 → 发布结果对账

- 前端 http://127.0.0.1:6800
- API http://127.0.0.1:11800
- 数据库环境变量 `SHIPGATE_DB`（容器内默认 `/data/shipgate.db`）

```bash
docker compose up -d --build
```

## 回退完成定义（唯一口径）

详见 [docs/回退残留完成定义.md](docs/回退残留完成定义.md)。采用严格收敛臂：

> 发版单置「已回滚」当且仅当实例册残留为 0。残留未清只能停在「回滚中」，
> 名册不切、对账不计已回滚、总览不计完成。

- 残留是实例册行属性（`instances.residual_release_id`），与健康探针正交，全局只此一套语义。
- 残留只能通过清理入口删除（健康页单行清理、回退页按发版单批量清理），探针改灯不抹残留。
- 名册（`app_env_versions`）只在残留清零的同一写事务内翻转，因此
  「已回滚 ⇔ 残留为 0 ⇔ 名册为回退目标版本」恒同时成立。
- 所有写口走 `BEGIN IMMEDIATE` 串行写事务，回退与实例心跳并发时结局确定、可重放。

### 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/发版单/{id}/回滚` | 回退写口，幂等；启动挂残留，清零即收敛 |
| GET | `/api/发版单/{id}/残留` | 该单残留行查询 |
| POST | `/api/发版单/{id}/清理残留` | 按发版单批量清理并再收敛 |
| POST | `/api/实例/{id}/清理残留` | 单行残留清理并再收敛（非残留行拒绝） |
| POST | `/api/实例/{id}/健康` | 探针写口，只改灯，不碰残留 |

对账 `/api/对账` 含「回退残留桶」「账实口径」「已回滚仍有残留单数」守卫；
总览 `/api/总览` 含「残留露口」。

## 测试

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd backend && python -m pytest
```

夹具分两套：`tests/test_rollback_residual_arm.py`（回退后留旧包）、
`tests/test_rollback_clean_arm.py`（清干净再标成功），
跨面不变量见 `tests/test_invariants.py`。
