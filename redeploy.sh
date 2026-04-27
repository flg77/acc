#!/bin/bash
git pull
podman rmi localhost/acc-agent-core:0.2.0 --force 
./acc-deploy.sh build
./acc-deploy.sh up
