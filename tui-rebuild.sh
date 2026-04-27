#/bin/bash
git pull 
podman-compose -f container/production/podman-compose.yml --profile tui build acc-tui
podman rm -f acc-tui 2>/dev/null || true
podman-compose -f container/production/podman-compose.yml --profile tui up -d acc-tui
sleep 5 && podman ps --filter name=acc-tui

podman attach acc-tui

