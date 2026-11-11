# PostgreSQL 并发实测

2026-11-11 在 GitHub Actions 的隔离 PostgreSQL 17.11 实例运行。Linux x86_64，4 个逻辑 CPU，Python 3.12.16。应用连接池为 2 个常驻连接和 2 个溢出连接。[CI 记录](https://github.com/yuchangceng065-hub/tripcanvas-shitu/actions/runs/43027368025) · [原始数据](benchmarks/postgres-contention-2026-11-11.json)

## 负载和结果

每档 72 次操作，重复 3 轮。确认负载并发调用实际的 `/planner/confirm`，从同一份未保存的一站式草案开始，包含鉴权、校验和数据库事务。事件负载直接并发调用 `RunRecorder.event`，向同一运行追加事件。表中的吞吐和 P95 分别取三轮测量的中位数。

| 负载 | 并发 | 吞吐（次/秒） | P95（ms） | 错误率 | 有锁等待的采样 / 总采样 | 同时等待峰值 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 同一行程首次确认与重试 | 1 | 89.0 | 12.3 | 0% | 0 / 223 | 0 |
| 同一行程首次确认与重试 | 4 | 86.0 | 63.1 | 0% | 16 / 197 | 1 |
| 同一行程首次确认与重试 | 12 | 85.6 | 198.8 | 0% | 75 / 175 | 2 |
| 同一行程首次确认与重试 | 24 | 79.4 | 423.2 | 0% | 65 / 167 | 2 |
| 同一运行追加事件 | 1 | 312.2 | 3.5 | 0% | 0 / 68 | 0 |
| 同一运行追加事件 | 4 | 323.8 | 22.3 | 0% | 54 / 64 | 3 |
| 同一运行追加事件 | 12 | 319.1 | 129.7 | 0% | 52 / 66 | 3 |
| 同一运行追加事件 | 24 | 298.3 | 173.5 | 0% | 52 / 72 | 3 |

- **确认 864 次。** 每轮首次写入一次，其余 71 次返回幂等重放。12 轮各保留一个行程版本和一条确认事件。新行程用主键冲突处理收敛到同一快照，已有行程仍按版本更新。
- **事件写入 864 次。** 每轮实际序号为连续的 1–72，均可按序读回。写入事务在运行记录的行锁内分配序号。
- **锁与尾延迟。** 观察连接约每 10 ms 查询一次 `pg_stat_activity` 中的 Lock 等待，记录同时等待的连接数。端到端 P95 包含连接池排队和应用开销。锁采样能观察竞争频率，精度受采样间隔影响。

测试使用进程内 ASGI，关闭外部服务调用，压测期间关闭确认接口的限流。数据适用于单行程或单运行的热点竞争场景。线上 HTTPS、Render 与 Neon 网络延迟、地图 API 和模型调用属于独立负载。

## 复跑

手动运行 **Postgres compatibility** 工作流，勾选 **Run the isolated PostgreSQL contention benchmark**，下载 `postgres-contention` 工件。也可以在启动隔离的本机 PostgreSQL 后运行以下命令。

```sh
TRIPCANVAS_BENCHMARK_DATABASE_URL='postgresql+psycopg://postgres:test@localhost:5432/tripcanvas' \
  .venv/bin/python scripts/benchmark_postgres.py --output /tmp/postgres-contention.json
```
