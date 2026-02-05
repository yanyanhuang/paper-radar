# PaperRadar - NAS Docker 部署指南

## 部署方式选择

| 方式 | 优点 | 适用场景 |
|------|------|----------|
| **方式 A: Docker Hub 拉取** | 无需构建，快速部署，易于更新 | 推荐，适合大多数用户 |
| **方式 B: 本地构建** | 可自定义修改 | 需要修改代码时使用 |

---

## 方式 A: 从 Docker Hub 拉取（推荐）

### 1. 准备文件

在 NAS 上创建目录并准备配置文件：

```bash
mkdir -p /volume1/docker/paper-radar
cd /volume1/docker/paper-radar
```

需要的文件（共 3 个）：

```
paper-radar/
├── docker-compose.nas.yml  # 从 GitHub 下载
├── config.yaml             # 从 GitHub 下载并修改
└── .env                    # 从 .env.example 创建
```

### 2. 创建 .env 配置文件

```bash
# 创建 .env 文件
cat > .env << 'EOF'
# LLM 配置
LIGHT_LLM_API_BASE=https://api.deepseek.com/v1
LIGHT_LLM_API_KEY=your-api-key
LIGHT_LLM_MODEL=deepseek-chat

HEAVY_LLM_API_BASE=https://generativelanguage.googleapis.com/v1beta/openai
HEAVY_LLM_API_KEY=your-gemini-api-key
HEAVY_LLM_MODEL=gemini-2.0-flash

# EZproxy 配置（访问 Nature 等付费期刊）
HKU_LIBRARY_UID=your-library-uid
HKU_LIBRARY_PIN=your-library-pin
EOF

# 编辑填写实际值
nano .env
```

### 3. 启动服务

```bash
# 拉取镜像并启动
docker-compose -f docker-compose.nas.yml pull
docker-compose -f docker-compose.nas.yml up -d

# 查看日志
docker-compose -f docker-compose.nas.yml logs -f
```

### 4. 更新镜像

当 Mac 端推送新版本后，NAS 端更新：

```bash
cd /volume1/docker/paper-radar

# 拉取最新镜像
docker-compose -f docker-compose.nas.yml pull

# 重启容器
docker-compose -f docker-compose.nas.yml up -d --force-recreate
```

---

## 方式 B: 本地构建

### 1. 准备文件

将以下文件上传到 NAS（例如 `/volume1/docker/paper-radar/`）：

```
paper-radar/
├── .env                    # 环境变量（从 .env.example 复制并填写）
├── config.yaml             # 配置文件
├── docker-compose.yml      # Docker Compose 配置
├── Dockerfile              # Docker 镜像构建文件
├── pyproject.toml          # Python 依赖
├── *.py                    # 所有 Python 文件
├── agents/                 # agents 目录
├── models/                 # models 目录
└── web/                    # 前端静态文件
```

### 2. 配置环境变量

在 NAS 上创建 `.env` 文件：

```bash
# 复制示例文件
cp .env.example .env

# 编辑填写实际值
nano .env
```

必填配置项：

```env
# LLM 配置
LIGHT_LLM_API_BASE=https://api.deepseek.com/v1
LIGHT_LLM_API_KEY=your-api-key
LIGHT_LLM_MODEL=deepseek-chat

HEAVY_LLM_API_BASE=https://generativelanguage.googleapis.com/v1beta/openai
HEAVY_LLM_API_KEY=your-gemini-api-key
HEAVY_LLM_MODEL=gemini-2.0-flash

# EZproxy 配置（访问 Nature 等付费期刊）
HKU_LIBRARY_UID=your-library-uid
HKU_LIBRARY_PIN=your-library-pin
```

### 3. 构建并启动

```bash
# 进入项目目录
cd /volume1/docker/paper-radar

# 构建镜像
docker-compose build

# 启动容器（后台运行）
docker-compose up -d

# 查看日志
docker-compose logs -f
```

---

## Mac 端开发工作流

### 首次设置

```bash
# 登录 Docker Hub
docker login
```

### 推送更新

```bash
# 修改代码后，构建并推送到 Docker Hub
./scripts/build-and-push.sh

# 或推送带版本号的镜像
./scripts/build-and-push.sh v1.0.0
```

---

## 测试运行

```bash
# 手动触发一次运行（使用对应的 compose 文件）
docker-compose -f docker-compose.nas.yml exec paper-radar python main.py --debug --dry-run

# 或者设置 RUN_ON_START=true 重启容器
```

---

## 定时任务

容器内置 cron 定时任务，默认每天 9:00 (Asia/Shanghai) 运行。

修改时间：编辑 `config.yaml` 中的 `runtime.schedule`：

```yaml
runtime:
  schedule: "0 9 * * *"  # cron 格式：分 时 日 月 周
```

---

## 网页前端

容器内置轻量 Web 服务，默认端口 `8000`：

```
http://<VPS_IP>:8000
```

如需修改端口，设置环境变量：

```env
WEB_PORT=8000
```

并在 `docker-compose` 中同步端口映射：

```yaml
ports:
  - "8000:8000"
```

---

## 常用命令

```bash
# 查看容器状态
docker-compose -f docker-compose.nas.yml ps

# 查看实时日志
docker-compose -f docker-compose.nas.yml logs -f

# 重启容器
docker-compose -f docker-compose.nas.yml restart

# 停止容器
docker-compose -f docker-compose.nas.yml down

# 查看镜像版本
docker images rockhhhh/paper-radar
```

---

## 数据持久化

以下目录会持久化到 NAS：

| 目录 | 说明 |
|------|------|
| `./reports/` | 生成的 Markdown 报告 |
| `./logs/` | 运行日志 |
| `./cache/` | PDF 缓存和 EZproxy cookies |

---

## 故障排查

### 查看日志
```bash
# 容器日志
docker-compose -f docker-compose.nas.yml logs --tail=100

# 应用日志
cat logs/paper-radar-$(date +%Y-%m-%d).log
```

### EZproxy 认证失败
```bash
# 删除缓存的 cookies，重新登录
rm -f cache/ezproxy_cookies.pkl
```

### 重新拉取镜像
```bash
docker-compose -f docker-compose.nas.yml pull
docker-compose -f docker-compose.nas.yml up -d --force-recreate
```
