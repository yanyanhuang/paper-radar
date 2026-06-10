#!/bin/bash
# PaperRadar Entrypoint Script
# Reads schedule from config.yaml and sets up cron dynamically

echo "=== PaperRadar ==="
echo "Starting at: $(date)"
echo "Timezone: $TZ"
echo ""

# Export environment variables for cron (with export prefix and quoted values)
printenv | grep -E "^(LIGHT_|HEAVY_|MINERU_|HKU_|TZ=|CHROME|PATH=)" | while IFS='=' read -r name value; do
    echo "export ${name}=\"${value}\""
done > /app/.env.cron
chmod 600 /app/.env.cron
echo "Environment variables exported for cron"

# Read schedule from config.yaml (default: "0 10 * * *")
SCHEDULE=$(python3 -c "
import yaml
try:
    with open('/app/config.yaml', 'r') as f:
        config = yaml.safe_load(f)
    schedule = config.get('runtime', {}).get('schedule', '0 10 * * *')
    print(schedule)
except Exception as e:
    print('0 10 * * *')
" 2>/dev/null)

echo "Cron schedule: $SCHEDULE"

# Setup cron job dynamically
echo "$SCHEDULE . /app/.env.cron && cd /app && /usr/local/bin/python main.py >> /app/logs/cron.log 2>&1" > /etc/cron.d/paper-radar
chmod 0644 /etc/cron.d/paper-radar
crontab /etc/cron.d/paper-radar
echo "Cron job configured"

# Start web server
WEB_PORT=${WEB_PORT:-8000}
echo "Starting web server on port ${WEB_PORT}..."
touch /app/logs/web.log
python -m uvicorn webapp:app --host 0.0.0.0 --port ${WEB_PORT} >> /app/logs/web.log 2>&1 &

# Run once immediately if requested
if [ "$RUN_ON_START" = "true" ]; then
    echo "Running initial execution..."
    cd /app && python main.py
fi

# Start cron daemon
echo "Starting cron daemon..."
cron

# Keep container running and tail logs
touch /app/logs/cron.log
tail -f /app/logs/cron.log
