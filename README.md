# 拾途 TripCanvas

面向个人的城市慢游规划网页，使用 Vue 3 + TypeScript 和 FastAPI。首次打开是杭州西湖的可编辑示例行程。

## 当前实现

- 桌面三栏旅行画布、手机端行程 / 地图 / 助手切换。
- Leaflet + OpenStreetMap 真实地图，OSRM 步行 / 驾车道路轨迹、里程与用时。
- 日程切换、拖拽排序、按钮排序、跨日移动、时长编辑、地点增删与收藏。
- 必须保留 / 固定时间 / 解锁，时间冲突检测，固定返程检查。
- 明确标注的规则建议（轻松一点、室内替换、晚出发），修改预览后确认。
- 本地持久化、不可变版本记录和恢复；行程摘要、JSON 和独立 HTML 卡片导出。
- 配套 FastAPI 账号、HttpOnly 会话、用户归属校验、版本同步、快照分享与撤销。
- 可配置兼容 chat/completions JSON 协议的模型服务；输出校验及锁定保护。

## 已知边界

线上前端通过 HTTPS 连接 Render Free 的 FastAPI，数据存储在 Neon Free PostgreSQL。已验证注册登录、行程同步、版本记录、可撤销分享与道路路线。浏览器也保存本地副本。API：https://tripcanvas-api.onrender.com/api/v1/health。

地图与路线已完成真实调用验证，地点使用 OSM WGS84 坐标，来源见 docs/map-sources.json。路线失败时明确显示错误和估算标签。道路用时不包含实时交通、停车与景区步行衔接。地点选择仍限于杭州目录，尚未实现任意城市搜索。模型未配置。

线上 owner-private 前端通过 Python 路线代理访问 FOSSGIS，道路请求按日合并、串行限速并缓存 24 小时。未启动后端的本地开发体验可直连公共路线服务。公开多用户使用前需要选择满足容量的路线服务。

生成建议是“请求完成后返回结构化结果”；SSE 接口可读取已完成运行事件，尚未实现逐阶段后台持久任务。当前数据库使用 SQLAlchemy 自动创建初始表；后续变更应加入 Alembic 迁移。Docker/PostgreSQL 配置已提供，但本次自动化验证使用 SQLite。

气象、营业时间、预约和票价没有实时核验。费用为示例个人预算。图片是旅行氛围配图，不是对应景点实景。旅行卡片文件和摘要是独立副本，发送后不能远程撤销；服务端分享链接支持撤销。

## 本地启动

需要 Node.js 22.12+（本次使用 Node.js 24）和 Python 3.12+。

```sh
npm ci
npm run dev
```

前端访问 http://127.0.0.1:5173 。浏览器本地体验无需 API 密钥。

另开终端启动后端：

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

后端启动后刷新前端，点右上角账号按钮注册。接口文档：http://127.0.0.1:8000/docs 。本地示例数据库创建在 backend/tripcanvas.db，不提交仓库。

模型及地图环境变量见 `.env.example`；通过运行环境注入到后端。密钥不得使用 VITE_ 前缀。跨源部署需要调整 FRONTEND_ORIGINS、HTTPS 会话及反向代理；推荐把前端 /api 代理到 Python 服务，保持同源。

## PostgreSQL

安装 Docker，并在未提交的 `.env` 中设置 `POSTGRES_PASSWORD` 后：

```sh
docker compose up --build
```

API 仍在 8000 端口，前端使用 npm run dev。不要将示例配置直接当作互联网生产环境配置。

## 校验

```sh
npm test
npm run build
.venv/bin/python -m pytest backend/tests -q
```

前端测试覆盖锁定保护、仅修改指定日期、时间冲突和未知指令。后端覆盖会话、用户隔离、不可变版本、幂等同步、分享快照、撤销和来源校验。

## 目录

- `src/App.vue`：三个主要页面与交互。
- `src/components/TripMap.vue`：Leaflet 地图、真实道路轨迹与路线标记。
- `src/domain.ts`：行程、锁定、计算与规则建议。
- `src/store.ts`：本地持久化和版本管理。
- `src/api.ts`：后端适配。
- `backend/app.py`：账号、同步、分享和模型接口。
- `backend/tests/`：后端自动化测试。
- `docs/`：设计与验证记录。

## 图片与图形

地图 © OpenStreetMap contributors（ODbL）；路线由 FOSSGIS / OSRM 提供。Logo 和助手插画为本项目原创 SVG/CSS。氛围照片来自 Unsplash 图片服务：

- https://images.unsplash.com/photo-1470770841072-f978cf4d019e
- https://images.unsplash.com/photo-1509042239860-f550ce710b93
- https://images.unsplash.com/photo-1441974231531-c6227db76b6e
- https://images.unsplash.com/photo-1511818966892-d7d671e672a2

图标来自 Lucide（ISC）。界面已标注示例数据与氛围图片性质。

## 免费云端部署

采用 Render Free + Neon Free，配置在 `render.yaml`，步骤见 `docs/render-neon-deployment.md`。前端已加入有限时长的唤醒等待、状态提示和手动重连；失败的写请求不会自动重发。云端要求 PostgreSQL，不允许临时 SQLite。Render 服务和 Neon 数据库均已创建并验证，生产构建默认指向真实 API；VITE_API_BASE 可覆盖该公开地址。本地开发仍使用 Vite /api 代理。公开部署信息见 docs/deployment.json，不包含凭据。

此前 Railway 部署被新账号试用限制阻止，不再作为首选。
