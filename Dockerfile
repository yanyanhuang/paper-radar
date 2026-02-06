FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Use default Debian mirror (override with DEBIAN_MIRROR if needed)
ARG DEBIAN_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources

# Install system dependencies including Chromium for Selenium
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    wget \
    gnupg \
    ca-certificates \
    ghostscript \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Set Chrome binary path for Selenium
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Install uv for faster package management
RUN pip install uv

# Copy project files
COPY pyproject.toml README.md ./
COPY *.py ./
COPY agents/ ./agents/
COPY models/ ./models/
COPY web/ ./web/

# Install Python dependencies
RUN uv pip install --system -e .

# Create directories
RUN mkdir -p /app/logs /app/reports /app/cache

# Copy config (can be overridden by volume mount)
COPY config.yaml .

# Create entrypoint script that reads schedule from config.yaml
COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Environment variables (to be overridden)
ENV TZ=Asia/Shanghai
ENV RUN_ON_START=false

ENTRYPOINT ["/entrypoint.sh"]
