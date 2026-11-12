# 拾途杭州发布手册

公开前端由 GitHub Pages 的产物仓库 `main` 根目录发布。Render 从源码仓库 `main` 部署 FastAPI，Neon 提供 PostgreSQL。[公开页](https://yuchangceng065-hub.github.io/tripcanvas-demo/) · [源码仓库](https://github.com/yuchangceng065-hub/tripcanvas-shitu) · [产物仓库](https://github.com/yuchangceng065-hub/tripcanvas-demo) · [公开配置](deployment.json)

## 1. 选定提交并验证

在源码仓库选择已提交的版本，从该提交的干净检出目录运行 `bash scripts/verify.sh`。确认该版本的 GitHub CI 与 PostgreSQL CI 通过。涉及数据库变更时，在隔离 PostgreSQL 先验证空库迁移和旧库升级。

```sh
release_commit=$(git rev-parse HEAD)
bash scripts/verify.sh
python3 scripts/release_pages.py build --commit "$release_commit" --output ../tripcanvas-pages-dist
```

构建使用 Python 3.12+ 和 Node.js 22.12+。如需逐字节复现，使用线上 [`release.json`](https://yuchangceng065-hub.github.io/tripcanvas-demo/release.json) 记录的 Node/npm 版本。构建脚本从 Git 读取提交里的前端文件及共享地点目录 `backend/places.json`，在临时目录运行 `npm ci` 与 Vite 构建。CI 也运行这条独立构建路径。产物带 `.nojekyll` 和 `release.json`，记录源码完整 SHA、API 地址、锁文件哈希与每个公开资源的 SHA-256。输出目录应是新目录。

## 2. 部署 Render API

把选定提交合入源码仓库 `main`，等待 Render 自动部署。已有服务采用原生 Python。重建服务时，从仓库根目录用 `pip install -r backend/requirements.txt` 构建，用 `python -m backend.start` 启动，健康检查路径为 `/api/v1/health`。也可用仓库的 [Docker Blueprint](../render.yaml) 新建同类服务。两种入口都读取平台端口并运行单 worker。[Render 部署文档](https://render.com/docs/deploys)

Render 启动时先运行 Alembic。已知的旧版 ORM 建表结构会在校验后登记对应版本，再补齐字段。遇到未知结构时，启动会报错。生产用 PostgreSQL，LangGraph 检查点另由其库初始化。[数据库检查点与保留期](checkpoints.md)

### 环境变量

| 变量 | 配置位置 | 用途 |
| --- | --- | --- |
| `VITE_API_BASE` | Pages 构建时 | API 的公开 HTTPS 地址，包含 `/api/v1`。脚本默认使用现有 Render API |
| `DATABASE_URL` | Render，密钥 | Neon PostgreSQL 连接串，含 `sslmode=require` |
| `FRONTEND_ORIGINS` | Render | 逗号分隔的来源。公开版为 `https://yuchangceng065-hub.github.io`，Sites 预览保留已有来源 |
| `COOKIE_SECURE` | Render | HTTPS 部署设为 `1` |
| `PLANNER_MODE` | Render | `auto` 启用已配置的模型，`rules` 只启用规则 |
| `MODEL_BASE_URL`、`MODEL_NAME` | Render | 当前 Groq 接口 `https://api.groq.com/openai/v1`，模型 `openai/gpt-oss-20b` |
| `GROQ_API_KEY` 或 `MODEL_API_KEY` | Render，密钥 | Groq 读取专用变量，其他兼容供应商读取通用变量 |
| `AMAP_WEB_KEY`、`AMAP_JS_KEY`、`AMAP_JS_SECURITY_CODE` | Render | 高德路线和地图配置。JS Key 使用平台域名限制；Web 服务 Key 和安全密钥留在服务器 |
| `ROUTING_PROVIDER`、`PUBLIC_API_ORIGIN` | Render | 路线供应商选择和高德 JS 安全代理的 API 根地址 |

限流与供应商预算见 [配置示例](../.env.example)。Render 自动提供 `PORT`、`RENDER_GIT_COMMIT`、`RENDER_EXTERNAL_HOSTNAME`。现有 API 在指定 Render 主机上也会补齐 Pages 来源。只把表里的公开 API 地址交给 Vite。[Render 环境变量说明](https://render.com/docs/environment-variables)

## 3. 发布 Pages 产物

先访问 API 的 `/api/v1/health`，确认 `commit` 匹配选定版本，`status=ok` 且 `storage=postgresql`。然后将构建目录同步到独立的 Pages 产物仓库。

```sh
git clone https://github.com/yuchangceng065-hub/tripcanvas-demo.git ../tripcanvas-demo-release
rsync -a --delete --exclude=.git ../tripcanvas-pages-dist/ ../tripcanvas-demo-release/
git -C ../tripcanvas-demo-release status --short
git -C ../tripcanvas-demo-release add -A
git -C ../tripcanvas-demo-release diff --cached --stat
git -C ../tripcanvas-demo-release commit -m "Publish TripCanvas $release_commit"
git -C ../tripcanvas-demo-release push origin main
```

上面的同步目标是专用静态产物仓库。Pages 发布源为 `main` / `/(root)`，使用现有 GitHub 发布权限。[GitHub Pages 发布源说明](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)

## 4. 验证与回滚

```sh
python3 scripts/release_pages.py verify --commit "$release_commit"
```

脚本用 curl 7.71+ 从公开 URL 下载资源并重试临时网络错误，校验首页和静态文件哈希、后端提交、PostgreSQL 健康状态以及 Pages 跨域预检。CDN 更新后，用独立浏览器会话再跑注册、规划、确认保存和刷新后的行程列表。以下完整链路采集会保留独立 QA 账号的运行记录和一条测试行程。

```sh
.venv/bin/python scripts/capture_live_run.py --mode full \
  --allowed-place-ids quyuan,museum --release-commit "$release_commit" \
  --output /tmp/tripcanvas-release-capture.json
```

遇到前后端不匹配时，先从 `release.json` 和 `/health` 找到当前提交。回退时，在 Render 选择已验证的旧部署，同时在产物仓库创建回退提交，恢复同一版本的静态文件与 manifest。复测跨域与确认接口。数据库迁移涉及破坏性改动时，先按迁移记录核对兼容性和备份。
