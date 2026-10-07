#!/bin/bash
# ============================================
# TD Station 一键部署脚本（腾讯云轻量服务器 Ubuntu）
# 用法：sudo bash deploy_tdstation.sh
# 执行前把下面 YOUR_DOMAIN 改成你的域名（如 td.omaleai.qzz.io）
# ============================================
set -e

# ---------- 修改这里 ----------
YOUR_DOMAIN="YOUR_DOMAIN"          # 例如 td.omaleai.qzz.io；暂无域名可留空，用 http://<服务器IP>:8000 临时访问
# 也可用第一个参数传入域名：sudo bash deploy_tdstation.sh td.omaleai.qzz.io
YOUR_DOMAIN="${1:-$YOUR_DOMAIN}"
TOKENDANCE_API_KEY=""              # TokenPay 申请到的 Key；留空则走 BYOK（用户各自授权钱包）
# ------------------------------

APP_DIR=/opt/tdstation
PORT=8000

echo "==> 1/5 安装依赖"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git nginx

echo "==> 2/5 拉取代码"
sudo mkdir -p $APP_DIR
sudo git clone https://github.com/OMaleZJH/tdstation.git $APP_DIR 2>/dev/null || true
cd $APP_DIR
sudo git pull origin main 2>/dev/null || true

echo "==> 3/5 创建虚拟环境并安装依赖"
sudo python3 -m venv venv
sudo ./venv/bin/pip install --upgrade pip -q
sudo ./venv/bin/pip install -r requirements.txt -q

echo "==> 4/5 注册 systemd 服务"
if [ -z "$YOUR_DOMAIN" ]; then
    APP_URL_VAL="http://127.0.0.1:8000"
    CALLBACK_URL_VAL="http://127.0.0.1:8000/api/auth/callback"
else
    APP_URL_VAL="https://$YOUR_DOMAIN"
    CALLBACK_URL_VAL="https://$YOUR_DOMAIN/api/auth/callback"
fi

sudo tee /etc/systemd/system/tdstation.service > /dev/null <<EOF
[Unit]
Description=TD Station (TokenDance AI Gateway)
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=$APP_DIR
Environment=APP_URL=$APP_URL_VAL
Environment=CALLBACK_URL=$CALLBACK_URL_VAL
Environment=TOKENDANCE_API_KEY=$TOKENDANCE_API_KEY
ExecStart=$APP_DIR/venv/bin/uvicorn app:app --host 127.0.0.1 --port $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo chown -R www-data:www-data $APP_DIR
sudo systemctl daemon-reload
sudo systemctl enable --now tdstation
sleep 2
echo "==> 服务状态："
systemctl status tdstation --no-pager | head -5

echo "==> 5/5 配置 nginx 反向代理"
if [ ! -z "$YOUR_DOMAIN" ]; then
    sudo tee /etc/nginx/sites-available/tdstation > /dev/null <<EOF
server {
    listen 80;
    server_name $YOUR_DOMAIN;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        proxy_buffering off;   # 必须：SSE 流式输出
        proxy_cache off;
    }
}
EOF
    sudo ln -sf /etc/nginx/sites-available/tdstation /etc/nginx/sites-enabled/tdstation
    sudo nginx -t && sudo systemctl reload nginx
    echo "==> nginx 已配置：http://$YOUR_DOMAIN（HTTPS 请用 certbot 签发：sudo certbot --nginx -d $YOUR_DOMAIN）"
else
    echo "==> 未配置域名，临时访问：http://<服务器IP>:$PORT"
fi

echo ""
echo "完成！验证：curl -s http://127.0.0.1:$PORT/api/auth/status"
echo "记得在 TokenPay 后台把 App URL 填为：$APP_URL_VAL"
