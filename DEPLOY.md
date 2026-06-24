# Sleeping fox — 阿里云 ECS 部署指南

## 一、创建 ECS 实例

1. 登录 [阿里云控制台](https://ecs.console.aliyun.com/)
2. 点击 **创建实例**
3. 配置如下：

| 配置项 | 推荐值 |
|--------|--------|
| 地域 | 离你最近的（如 华东1 杭州） |
| 实例规格 | 2 vCPU / 2 GiB 内存（ecs.e-c2m1.large 或类似） |
| 操作系统 | Ubuntu 22.04 LTS 64位 |
| 系统盘 | 40 GiB ESSD Entry |
| 网络 | 默认 VPC + 按量付费公网 IP |
| 带宽 | 按量计费，峰值 5 Mbps |
| 安全组 | 开放 22 (SSH), 80 (HTTP), 443 (HTTPS) |

4. **关键：安全组规则**

| 端口 | 协议 | 来源 | 说明 |
|------|------|------|------|
| 22 | TCP | 0.0.0.0/0 | SSH 登录 |
| 80 | TCP | 0.0.0.0/0 | HTTP |
| 8080 | TCP | 0.0.0.0/0 | Sleeping fox（对外服务） |

5. 设置 root 密码或 SSH 密钥，确认订单

> 💰 费用估算：约 ¥60-100/月

---

## 二、登录并初始化

拿到 ECS 公网 IP 后：

```bash
# 本地终端执行
ssh root@<ECS公网IP>
```

```bash
# === 以下在 ECS 上执行 ===

# 更新系统
apt update && apt upgrade -y

# 安装基础工具
apt install -y python3 python3-pip python3-venv git nginx curl

# 创建项目目录
mkdir -p /opt/apps
cd /opt/apps

# 克隆代码
git clone https://github.com/blackmoon-art/jack.git sleeping-fox
cd sleeping-fox
```

---

## 三、配置 Python 环境

```bash
cd /opt/apps/sleeping-fox

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install openai python-dotenv fastapi uvicorn
# 可选
pip install akshare yfinance matplotlib

# 安装中文字体（matplotlib 图表中文显示）
yum install -y wqy-microhei-fonts langpacks-core-font-zh_CN

# 清除 matplotlib 字体缓存
python -c "
import matplotlib, os
cache = matplotlib.get_cachedir()
for f in os.listdir(cache):
    path = os.path.join(cache, f)
    if os.path.isfile(path): os.remove(path)
"
```

---

## 四、配置环境变量

```bash
vi .env
```

填入以下内容（参考本地 `.env`）：

```ini
# ── LLM ──
AGENT_PROVIDER=deepseek
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-v4-flash

# ── Agent ──
AGENT_MAX_ITERATIONS=10
AGENT_MAX_TOKENS=8000
AGENT_BASH_TIMEOUT=120
AGENT_WORK_DIR=/opt/apps/agent_workspace
AGENT_MEMORY_WINDOW=10
DAILY_LIMIT_PER_USER=20

# ── Web ──
WEB_ACCESS_CODE=你的访问密码
```

```bash
# 创建工作目录
mkdir -p /opt/apps/agent_workspace
```

---

## 五、systemd 服务（开机自启 + 崩溃重启）

```bash
cat > /etc/systemd/system/sleeping-fox.service << 'EOF'
[Unit]
Description=Sleeping fox Web UI
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/apps/sleeping-fox
Environment=PATH=/opt/apps/sleeping-fox/.venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=WEB_PORT=8080
ExecStart=/opt/apps/sleeping-fox/.venv/bin/python /opt/apps/sleeping-fox/web/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 启动服务
systemctl daemon-reload
systemctl enable sleeping-fox
systemctl start sleeping-fox

# 确认运行
systemctl status sleeping-fox
curl http://localhost:8080/api/health
```

---

## 六、（可选）Nginx 反代 + 防火墙

如果不想暴露 8080 端口，用 Nginx 反代到 80：

```bash
cat > /etc/nginx/sites-available/sleeping-fox << 'EOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
        proxy_buffering off;
    }
}
EOF

ln -sf /etc/nginx/sites-available/sleeping-fox /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

> 用了 Nginx 后，安全组只需要开放 80 端口，8080 可以关掉。

---

## 七、验证部署

```bash
# 本地访问
curl http://<ECS公网IP>/api/health
# 浏览器打开
# http://<ECS公网IP>
```

---

## 八、日常维护

```bash
# 查看日志
journalctl -u sleeping-fox -f

# 重启服务
systemctl restart sleeping-fox

# 更新代码
cd /opt/apps/sleeping-fox
git pull
systemctl restart sleeping-fox
```
