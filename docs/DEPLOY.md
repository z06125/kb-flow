# 国内服务器部署实录（阿里云轻量 2C2G 为例）

## 1. 基础环境

```bash
# swap（2G 内存机器建议必配）
swapon --show || { fallocate -l 2G /swapfile && chmod 600 /swapfile && \
  mkswap /swapfile && swapon /swapfile && \
  echo "/swapfile none swap sw 0 0" >> /etc/fstab; }

# 时区
timedatectl set-timezone Asia/Shanghai

# SSH 只留密钥登录
sed -i "s/^#\?PasswordAuthentication.*/PasswordAuthentication no/" /etc/ssh/sshd_config
systemctl reload sshd
```

## 2. Docker 与国内镜像加速

大陆服务器拉不到 Docker Hub，配置加速器（2026-11 实测可用）：

```bash
apt-get install -y docker.io docker-compose-v2
cat > /etc/docker/daemon.json <<'EOF'
{ "registry-mirrors": ["https://docker.m.daocloud.io", "https://docker.1ms.run"] }
EOF
systemctl restart docker
docker pull python:3.12-slim   # 验证
```

## 3. 防火墙

阿里云轻量控制台 → 防火墙 → 添加规则：`TCP 8000`（kb-core Web UI）。
SSH 保持 22 默认即可，其余端口不开。

## 4. 启动

```bash
cd /opt/kb
cp .env.example .env && vi .env        # BIGMODEL_API_KEY 必填
docker compose up -d --build
docker compose ps
curl -s localhost:8000/healthz
grep KB_TOKEN .env                     # 访问令牌
```

## 5. 日常运维

```bash
docker compose logs -f worker          # 看知识卡流水线
docker compose logs feeds | tail -20   # 看采集
vi data/feeds.json                     # 改订阅源（1小时内生效）
docker compose exec worker python scripts/ingest_docs.py   # 文档入库（先放文件到 data/inbox/）
docker compose restart bot             # 配置 TG_BOT_TOKEN 后
```

## 6. Telegram 反代（大陆服务器必备）

服务器直连不了 api.telegram.org。到 Cloudflare（免费）部署一个 Worker
粘贴 `cf-worker/tg-relay.js`，然后 `.env` 加：

```
TG_API_BASE=https://<worker名>.<子域>.workers.dev
```

`docker compose restart bot`。给 bot 发 `/start` 绑定即用。
