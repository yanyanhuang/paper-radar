# PaperRadar

PaperRadar 是一个基于关键词的「论文雷达」：每天自动抓取 arXiv +（可选）学术期刊最新论文，通过“双 LLM”完成筛选与 PDF 深度分析，生成日报（Markdown + JSON），并通过内置 Web UI 快速浏览与检索。

## 功能亮点

- 多源抓取：arXiv RSS + 期刊 RSS（Nature/NEJM/Cell/Science 等，可在 `config.yaml` 关闭）
- 双 LLM 架构：
  - Light LLM：基于 title/abstract 快速判断匹配哪些关键词（输出 `matched_keywords`）
  - Heavy 多模态 LLM：读取 PDF，输出 TLDR/贡献/方法/实验/创新/局限/数据/代码 + 质量评分
- 领域总结：每个领域一段 Markdown 总结，引用“论文1/2/3…”（Web UI 中可点击跳转到对应论文卡片）
- 报告输出：每日生成 `reports/`（Markdown）与 `reports/json/`（JSON）
- Web UI：日期/领域筛选、搜索、排序、可跳转引用数字、分页加载
- Docker 部署：容器内 cron 定时运行 + FastAPI Web 服务（默认端口 `8000`）

## 工作流（Stage 0-4）

1. Stage 0：抓取论文元数据（title/abstract/pdf_url…）
2. Stage 1：Light LLM 关键词匹配（输出 `matched_keywords`）
3. Stage 2：Heavy LLM 读取 PDF 深度分析（结构化字段 + `quality_score`）
4. Stage 3：SummaryAgent 生成领域总结（Markdown，引用“论文N”）
5. Stage 4：Reporter 保存 Markdown/JSON；Web UI 读取 JSON 展示

## 快速开始（Docker，本地构建）

### 1) 配置环境变量

```bash
cp .env.example .env
nano .env
```

### 2) （可选）调整 `config.yaml`

- 不需要期刊：将 `journals.enabled: false`，并可关闭 `ezproxy.enabled: false`
- 调整定时：`runtime.schedule`（cron 表达式，容器时区见 `TZ`）

### 3) 启动

```bash
docker compose up -d --build
```

### 4) 访问 Web UI

- `http://localhost:8000`
- 健康检查：`http://localhost:8000/api/health`

### 5) 立即跑一遍（可选）

```bash
docker compose exec paper-radar python main.py --dry-run
```

生成的日报会写入 `reports/` 与 `reports/json/`，Web UI 会自动显示可选日期。

## 关键配置

### 环境变量（`.env`）

| 变量 | 说明 |
| --- | --- |
| `LIGHT_LLM_API_BASE` / `LIGHT_LLM_API_KEY` / `LIGHT_LLM_MODEL` | 轻量 LLM（OpenAI compatible） |
| `HEAVY_LLM_API_BASE` / `HEAVY_LLM_API_KEY` / `HEAVY_LLM_MODEL` | 多模态 LLM（PDF 分析） |
| `HKU_LIBRARY_UID` / `HKU_LIBRARY_PIN` | （可选）EZproxy 凭据（访问付费期刊 PDF） |
| `TZ` | 容器时区（默认 `Asia/Shanghai`） |
| `WEB_PORT` | Web 端口（默认 `8000`） |
| `RUN_ON_START` | 容器启动时立即运行一次（默认 `false`） |

### `config.yaml`

- `keywords`: 领域列表（`name` / `description` / `examples`）
- `preprints`: 预印本源配置（`arXiv` + `bioRxiv/medRxiv`）
- `journals`: 期刊源开关与列表
- `llm`: light/heavy/summary 的模型与限速
- `runtime`: cron schedule、并发、超时等
- `output`: markdown/json 输出路径

## 部署到 VPS / NAS

- 详细步骤见 `DEPLOY.md`
- 生产建议：将 `8000` 端口置于 Nginx/Caddy 反代之后再开放公网访问（HTTPS + 访问控制）

## 文档

- `DESIGN.md`：整体流程、模块职责与设计说明

## License

MIT (see `LICENSE`)
