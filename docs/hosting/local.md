# Local Hosting guide

## Arch linux

1. install docker, docker-compose, docker-buildx
2. sudo systemctl enable --now docker
3. sudo usermod -aG docker $USER
4. sudo chown $USER /var/run/docker.sock
5. docker ps