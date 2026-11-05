# LangGraph 检查点与备份

对话/规划使用 LangGraph 的 PostgreSQL Checkpointer。检查点表**由库自动创建**，不在 Alembic 迁移里：

- `checkpoints`、`checkpoint_blobs`、`checkpoint_writes`、`checkpoint_migrations`（以实际安装版本为准）
- 应用启动时 `AsyncPostgresSaver.setup()` 幂等建表；连接池最多 3 次退避重试，失败则本次启动失败，不会静默退回内存。

## 备份与恢复

- 备份必须包含上述表和业务表（`planning_sessions`、`runs`、`run_events` 等）在同一个 `pg_dump` 里，避免“旅行状态有了、检查点没有”。
- 恢复时整套恢复；不要让应用先建一半表再从备份补。恢复后启动一次应用，让 `setup()` 校验/补齐。
- Alembic 只覆盖业务表；不要把 `alembic upgrade head` 当成检查点表的迁移工具。
- 升级 `langgraph-checkpoint-postgres` 后先在 staging 跑一次启动 + 恢复续接，再上生产。

## 运维检查

```sql
SELECT count(*) FROM checkpoints;
SELECT count(*) FROM planning_sessions WHERE status IN ('running','needs_input','review');
```

- 单实例限制：进程内锁、后台任务和检查点连接池按单 worker 设计；多实例前需要集中限流/任务队列。
- 旧 thread 目前没有自动 TTL；需要时按 `checkpoints.created_at` 做保留期清理，并先确认没有待继续任务。
- 检查点里含用户对话与工具观察，不含密钥；数据库访问权限按账号隔离，导出/删除需走运维流程。

## 本机开发

SQLite 使用 `tripcanvas.db.agent.db` 持久检查点；删除该文件会丢失“继续原任务”的能力，业务表不受影响。
