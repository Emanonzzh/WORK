#!/usr/bin/env bash
# One-shot deploy for rs-assistant (run ON the ECS server, inside the repo dir):
#   cd <repo dir on server> && git pull && bash deploy.sh
# It preserves DASHSCOPE_API_KEY from the currently running container.
set -euo pipefail
cd "$(dirname "$0")"

echo "== 1/5 git pull =="
git pull --ff-only

echo "== 2/5 find old container =="
CONTAINER=$(docker ps -a --format '{{.Names}}' | grep -E '^rs-(app|assistant)$' | head -1 || true)
echo "old container: ${CONTAINER:-none}"

KEY=""
if [ -n "$CONTAINER" ]; then
  KEY=$(docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^DASHSCOPE_API_KEY=//p' | head -1)
fi
if [ -n "$KEY" ]; then
  echo "DASHSCOPE_API_KEY: recovered from old container (${#KEY} chars)"
else
  echo "!! DASHSCOPE_API_KEY not found in old container"
fi

echo "== 3/5 docker build =="
docker build -t rs-assistant .

echo "== 4/5 restart container =="
if [ -n "$CONTAINER" ]; then
  docker rm -f "$CONTAINER"
fi
if [ -n "$KEY" ]; then
  docker run -d --name rs-app -p 8501:8501 -e "DASHSCOPE_API_KEY=$KEY" rs-assistant
else
  echo "!! Run manually with your key:"
  echo "   docker run -d --name rs-app -p 8501:8501 -e DASHSCOPE_API_KEY=your-key rs-assistant"
  exit 1
fi

echo "== 5/5 health check =="
sleep 8
curl -s -o /dev/null -w "http_code=%{http_code}\n" http://127.0.0.1:8501 || true
docker ps --filter name=rs-app --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
echo "Done. Public URL: http://47.76.101.97:8501"
