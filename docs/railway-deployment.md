# Railway Python 部署

状态：配置已完成、本地验证通过；尚未部署，待当前会话可访问 Railway。

1. 在 Railway 创建 TripCanvas 项目、PostgreSQL 服务和 Python API 服务。
2. API 服务上传本目录源码，根目录保持仓库根目录。railway.json 指定 backend/Dockerfile；启动命令 python -m backend.start 自动读取 PORT。
3. API 环境变量：
   - DATABASE_URL：引用 PostgreSQL 服务的 DATABASE_URL。支持 Railway 的 postgresql:// 格式，自动转换为 psycopg 驱动。
   - FRONTEND_ORIGINS：https://tripcanvas-shitu-melody.yuchangceng065.chatgpt.site
   - COOKIE_SECURE：1
   - MODEL_BASE_URL / MODEL_API_KEY / MODEL_NAME：仅在启用模型时配置在服务端。
4. 保持单副本、单 worker。公共 FOSSGIS 路线接口最多每 1.1 秒发起一次请求，缓存 24 小时、最多 256 项，最多 8 个排队请求。扩容前需集中限流或换用自托管 / 商业路线服务。
5. 健康检查 /api/v1/health 会检查数据库；Railway 缺少 DATABASE_URL 时拒绝启动，以免误用临时磁盘 SQLite。
6. 生成 HTTPS 域名，验证 health 返回 storage=postgresql。注册测试账号，验证登录、跨源预检、同步、刷新会话、撤销分享和退出登录。
7. 前端构建环境设置 VITE_API_BASE=https://实际服务域名/api/v1，再通过 Sites 工作流构建、发布。这个变量是公开地址，不能放密码或密钥。

跨站登录同时支持 HttpOnly cookie 和不依赖第三方 Cookie 的 Bearer 会话；令牌只保存于 sessionStorage，数据库保存哈希，退出后撤销，最长 7 天。没有把数据库密码或模型密钥放到前端。

数据库当前使用 SQLAlchemy 创建初始表；后续结构变更应增加迁移。此轮单元测试使用 SQLite，PostgreSQL 云端验证须在部署后执行。

服务资料：
- https://docs.railway.com/guides/fastapi
- https://routing.openstreetmap.de/about.html
- https://operations.osmfoundation.org/policies/tiles/
