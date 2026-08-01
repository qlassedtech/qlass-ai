#!/bin/sh
set -e

# cron(8) only gives spawned jobs a minimal built-in environment, not this
# container's actual env (DATABASE_URL, REDIS_URL, ANTHROPIC_API_KEY, etc,
# all injected via docker-compose's env_file) — dump it to a file crontab's
# job lines explicitly `source` before running each script. -0/tr rebuilds
# real newlines from NUL-separated `printenv -0` output so a multi-line
# value can't break the file.
printenv -0 | tr '\0' '\n' > /etc/environment

mkdir -p /app/logs
exec cron -f
