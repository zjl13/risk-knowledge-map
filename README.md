# 科创企业风险知识地图（独立版）

本目录是完全独立的知识图谱项目，不修改、不依赖 ComplianceAI 的页面、路由或 MCP 清单。后续确认需要集成时，再通过独立 API 对接现有系统。

## 最快使用方式

### 在线同步版

双击 `启动在线图谱.cmd`。脚本会优先使用本目录的 `.venv`，其次使用上级项目已有的 `.venv`，并自动打开：

```text
http://127.0.0.1:8025/
```

在线页面顶部会显示“同步法宝”按钮。点击后按当前选中的风险类型调用北大法宝 MCP，并同步刷新离线数据包。

### 完全离线版

双击 `打开离线图谱.cmd`，或直接打开 `web/index.html`。

离线模式不启动后端，因此“同步法宝”按钮会保持禁用；节点筛选、中心聚焦、来源查看、缩放、布局保存等功能仍可正常使用。

## GitHub Pages 公开访问

`web/` 已整理为可直接发布的静态站点，`.github/workflows/pages.yml` 会在推送到 `main` 后自动发布。GitHub Pages 公开版仅提供缓存数据查询，不包含 Token，也不允许浏览器直接调用北大法宝 MCP。

首次发布需要：

```powershell
gh auth login
git init -b main
git add .
git commit -m "Publish standalone risk knowledge map"
gh repo create risk-knowledge-map --public --source . --remote origin --push
```

随后在新仓库的 **Settings → Pages → Source** 中选择 **GitHub Actions**。发布地址通常为 `https://你的用户名.github.io/risk-knowledge-map/`。

## 独立安装依赖

如果脱离上级项目单独复制本目录，可运行：

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

## 数据与隐私

- 当前离线包包含九类风险各 3 条北大法宝真实裁判文书缓存；
- 案例保留案号、案由、法院、裁判日期、摘要、风险判定信号和原文链接；
- Token 仅保存在本目录 `.env`，不会返回浏览器，也不会写入案例缓存或离线 JavaScript；
- 修改 `data/risk_map_seed.json` 或缓存后，可运行 `python scripts/build_offline_risk_map.py` 重建离线包。

## 独立 API

- `GET /api/health`
- `GET /api/risk-map`
- `GET /api/risk-map/pkulaw-status`
- `POST /api/risk-map/sync`

## 目录结构

```text
risk_knowledge_map/
├─ backend/       北大法宝 MCP 客户端与图谱构建
├─ data/          九类风险种子和本地案例缓存
├─ scripts/       离线数据构建脚本
├─ web/           零 CDN 的交互式图谱前端
├─ server.py      独立 FastAPI 服务
└─ requirements.txt
```
