# PaperRadar - 设计方案文档

## 项目概述

一个基于关键词的学术论文自动分析工具，部署在 NAS 上通过 Docker 运行。支持 **arXiv** 和 **学术期刊**（Nature 子刊、NEJM 等）两种来源。使用双 LLM 架构：轻量级 LLM 进行快速筛选，重量级多模态 LLM 进行 PDF 深度分析。

**核心特性**：
- 支持 arXiv 预印本和顶级期刊（Nature Medicine、Nature Methods 等）
- 通过 EZproxy 认证访问付费期刊 PDF
- 双 LLM 架构优化成本
- 自动生成领域进展总结报告

---

## 五阶段 Agent 工作流

```
┌─────────────────────────────────────────────────────────────────┐
│                     每日定时触发 (Cron)                           │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  Stage 0: 数据获取                                               │
│  ├─ 0.1 从 arXiv RSS 获取指定类别的新论文列表                      │
│  ├─ 0.2 从学术期刊 RSS 获取最新论文 (Nature, NEJM 等)              │
│  └─ 获取论文元数据 (标题、摘要、PDF链接)                           │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  Stage 1: 轻量级 LLM Agent (筛选匹配)                             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Light LLM (如 Qwen2.5-7B / GPT-4o-mini)                 │   │
│  │  ├─ 输入: 论文 title + abstract + 预设关键词列表           │   │
│  │  ├─ 任务: 判断是否匹配任一关键词，返回匹配的关键词           │   │
│  │  └─ 输出: { matched: bool, keywords: [...], reason: ""}  │   │
│  └──────────────────────────────────────────────────────────┘   │
│  批量处理所有论文，筛选出匹配的论文                                │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  Stage 2: 重量级多模态 LLM Agent (深度分析)                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Heavy Vision LLM (如 GPT-4o / Gemini-2.0-Flash)         │   │
│  │  ├─ 输入: 论文 PDF (base64) + 匹配的关键词                 │   │
│  │  ├─ 任务: 阅读完整 PDF，深度分析论文内容                    │   │
│  │  └─ 输出: {                                              │   │
│  │  │     title, authors, affiliations,                    │   │
│  │  │     tldr, key_contributions, methodology,            │   │
│  │  │     limitations, relevance_analysis                  │   │
│  │  │  }                                                   │   │
│  └──────────────────────────────────────────────────────────┘   │
│  仅处理 Stage 1 筛选出的论文                                      │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  Stage 3: 总结 Agent (领域进展综合)                               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Summary LLM (可复用 Light 或 Heavy LLM)                  │   │
│  │  ├─ 输入: 每个关键词下所有论文的深度分析结果                  │   │
│  │  ├─ 任务: 综合分析该领域的最新进展趋势                       │   │
│  │  └─ 输出: 领域进展总结报告                                  │   │
│  └──────────────────────────────────────────────────────────┘   │
│  按关键词分组，生成每个领域的综合总结                              │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  Stage 4: 报告生成与推送                                          │
│  ├─ 组装最终报告 (HTML / Markdown)                               │
│  └─ 推送 (邮件 / Webhook)                                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 项目结构

```
paper-radar/
├── main.py                    # 主程序入口，协调各 Agent
├── config.yaml                # 配置文件 (关键词 + 双 LLM 配置)
│
├── agents/
│   ├── __init__.py
│   ├── base.py                # LLM Client 基类
│   ├── filter_agent.py        # Stage 1: 轻量级筛选 Agent
│   ├── analyzer_agent.py      # Stage 2: 重量级分析 Agent
│   └── summary_agent.py       # Stage 3: 总结 Agent
│
├── fetcher.py                 # arXiv 论文获取
├── journal_fetcher.py         # 学术期刊论文获取 (Nature, NEJM 等)
├── pdf_handler.py             # PDF 下载 (含 EZproxy 认证)
├── reporter.py                # 报告生成与保存 (Markdown/JSON)
├── config_loader.py           # 配置加载器
│
├── models/
│   ├── __init__.py
│   └── paper.py               # 论文数据模型
│
├── paper_history.py           # 历史去重与追踪
├── webapp.py                  # FastAPI Web UI 服务
├── web/                       # 前端静态文件
│   ├── index.html
│   ├── app.js
│   └── styles.css
│
├── scripts/
│   ├── entrypoint.sh          # Docker 入口脚本（cron + web）
│   ├── build-and-push.sh      # 构建并推送镜像
│   └── test_sources.py        # 数据源连通性测试
│
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## 配置文件设计 (config.yaml)

```yaml
# ===========================================
# 关键词配置
# ===========================================
keywords:
  - name: "多模态大模型"
    description: "视觉语言模型、多模态理解与生成、图文交互"
    examples:
      - "VLM, MLLM, vision-language model"
      - "image captioning, visual question answering"
      - "multimodal reasoning, cross-modal learning"

  - name: "LLM Agent"
    description: "基于大语言模型的智能体、工具调用、自主决策"
    examples:
      - "autonomous agent, tool use, function calling"
      - "planning, reasoning, decision making"
      - "multi-agent, agent collaboration"

  - name: "RAG与知识增强"
    description: "检索增强生成、知识库、向量数据库"
    examples:
      - "retrieval augmented generation"
      - "knowledge base, vector database"
      - "embedding, semantic search"

  - name: "推理与思维链"
    description: "逻辑推理、Chain-of-Thought、数学推理"
    examples:
      - "chain of thought, step-by-step reasoning"
      - "mathematical reasoning, logical inference"
      - "self-consistency, tree of thought"

# ===========================================
# arXiv 配置
# ===========================================
arxiv:
  categories: "cs.AI+cs.CV+cs.CL+cs.LG+cs.MA"
  max_papers_per_day: 200

# ===========================================
# LLM 配置 - 双 Agent 架构
# ===========================================
llm:
  # 轻量级 LLM - 用于快速筛选匹配
  light:
    api_base: "https://api.siliconflow.cn/v1"
    api_key: "${LIGHT_LLM_API_KEY}"
    model: "Qwen/Qwen2.5-7B-Instruct"
    temperature: 0.1
    max_tokens: 500

  # 重量级多模态 LLM - 用于 PDF 深度分析
  heavy:
    api_base: "https://api.openai.com/v1"
    api_key: "${HEAVY_LLM_API_KEY}"
    model: "gpt-4o"
    temperature: 0.3
    max_tokens: 4000

  # 总结 Agent - 可复用 light 或 heavy
  summary:
    use: "light"  # 或 "heavy"
    temperature: 0.5
    max_tokens: 2000

# ===========================================
# 输出配置
# ===========================================
output:
  language: "Chinese"
  formats:
    markdown:
      enabled: true
      path: "./reports/"
    json:
      enabled: true
      path: "./reports/json/"

# ===========================================
# 运行配置
# ===========================================
runtime:
  schedule: "0 9 * * *"
  timezone: "Asia/Shanghai"
  retry_count: 3
  pdf_timeout: 60
  concurrent_analysis: 3
```

---

## Agent 实现规范

### 1. FilterAgent (轻量级筛选)

**输入**: 论文 title + abstract + 预设关键词列表

**输出 JSON 格式**:
```json
{
    "matched": true,
    "matched_keywords": ["关键词1", "关键词2"],
    "relevance": "high",
    "reason": "简短说明匹配原因"
}
```

**判断标准**:
- 只有当论文主题与关键词**高度相关**时才返回 matched: true
- 仅仅提到相关概念但主题不符的论文应返回 matched: false
- 可以匹配多个关键词

### 2. AnalyzerAgent (深度分析)

**输入**: 论文 PDF (base64) + 匹配的关键词

**输出 JSON 格式**:
```json
{
    "title": "论文完整标题",
    "authors": ["作者1", "作者2"],
    "affiliations": ["机构1", "机构2"],
    "tldr": "一句话总结",
    "contributions": ["贡献1", "贡献2"],
    "methodology": "技术方法简述",
    "experiments": "实验结果简述",
    "innovations": ["创新点1", "创新点2"],
    "limitations": ["局限1", "局限2"],
    "keyword_relevance": {
        "关键词": {
            "relation": "具体关联说明",
            "contribution_level": "high/medium/low"
        }
    }
}
```

### 3. SummaryAgent (领域总结)

**输入**: 每个关键词下所有论文的深度分析结果

**输出**: 纯文本格式的领域进展总结 (300-500字)

**内容要求**:
1. 今日概览: 论文数量和整体趋势
2. 重点突破: 最值得关注的1-2项研究
3. 技术趋势: 观察到的技术方向
4. 值得跟进: 建议深入阅读的论文

---

## Docker 部署配置

### 环境变量

| 变量名 | 说明 | 必填 |
|--------|------|------|
| LIGHT_LLM_API_BASE | 轻量级 LLM API 地址 | 是 |
| LIGHT_LLM_API_KEY | 轻量级 LLM API 密钥 | 是 |
| LIGHT_LLM_MODEL | 轻量级 LLM 模型名称 | 是 |
| HEAVY_LLM_API_BASE | 重量级 LLM API 地址 | 是 |
| HEAVY_LLM_API_KEY | 重量级 LLM API 密钥 | 是 |
| HEAVY_LLM_MODEL | 重量级 LLM 模型名称 | 是 |
| HKU_LIBRARY_UID | HKU 图书馆用户 ID | 否* |
| HKU_LIBRARY_PIN | HKU 图书馆 PIN | 否* |
| TZ | 时区 | 否 |

> *注: 如需访问 Nature 等付费期刊 PDF，则 EZproxy 凭据为必填

### 数据卷

- `./config.yaml:/app/config.yaml:ro` - 配置文件
- `./reports:/app/reports` - 报告输出
- `./logs:/app/logs` - 日志文件
- `./cache:/app/cache` - 缓存目录

---

## 设计优势

| 特性 | 说明 |
|------|------|
| 成本优化 | 轻量级 LLM 快速筛选，仅对匹配论文调用昂贵的多模态 API |
| 深度分析 | 多模态 LLM 直接阅读 PDF，获取完整上下文 |
| 语义理解 | LLM 判断相关性，比 embedding 更准确 |
| 灵活配置 | 双 LLM 独立配置，可混用不同服务商 |
| 领域洞察 | 专门的总结 Agent 提供领域级进展分析 |
| 可扩展 | 关键词可随时增减，无需重新训练 |
| 多源支持 | 同时支持 arXiv 预印本和顶级学术期刊 |
| 付费期刊访问 | 通过 EZproxy + Selenium 自动认证下载付费 PDF |

---

## EZproxy 认证机制

### 工作原理

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Nature PDF URL │ ──▶ │  EZproxy Login  │ ──▶ │  Proxied URL    │
│  (需要付费)      │     │  (Selenium)     │     │  (可访问)        │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                              │
                              ▼
                        ┌─────────────────┐
                        │  Cookies 缓存   │
                        │  (复用会话)      │
                        └─────────────────┘
```

### 支持的期刊

- **Nature 系列**: Nature Medicine, Nature Methods, Nature Communications 等
- **医学期刊**: NEJM, Lancet, Lancet Digital Health 等
- **其他**: Cell, Science, PNAS (可扩展)

### 使用方式

1. 在 `.env` 中配置 HKU 图书馆凭据
2. 在 `config.yaml` 中启用 `ezproxy.enabled: true`
3. 系统自动识别期刊论文并使用 EZproxy 认证下载
