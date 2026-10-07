# TD Station · TokenDance AI 中转站

参赛作品：接入 TokenPay（TokenDance）参与环球黑客松·AI应用赚钱挑战赛。
用户通过 OAuth 授权自己的 Token 钱包，即可用余额调用对话 / 图片 / 视频模型，调用自动携带 `X-App-URL` 归因头，用于比赛真实数据统计。

## 功能

- OAuth（Authorization Code + S256 PKCE）接入 TokenDance，授权即生成专属 API Key（仅存服务端）
- 对话：OpenAI Chat Completions 流式输出
- 图片：OpenAI Image Generations
- 视频：Vidu 文生视频（异步任务 + 轮询）
- 钱包：余额查询（微元换算元）、充值会话（支付宝 / PC 扫码）
- 模型目录：实时拉取 `/gateway/v1/models` 并按用途分类

## 本地运行

```bash
pip install -r requirements.txt
set APP_URL=http://127.0.0.1:8000
set CALLBACK_URL=http://127.0.0.1:8000/api/auth/callback
uvicorn app:app --host 0.0.0.0 --port 8000
```

浏览器打开 http://127.0.0.1:8000 ，点「连接 Token 钱包」完成授权即可。

## 部署到服务器（腾讯云 Ubuntu 示例）

```bash
# 1. 上传代码（scp / git 均可），进入目录
cd tdstation
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. 配置环境变量（关键！APP_URL 是 TokenPay 归因的唯一标识，必须唯一稳定且与后台提交的一致）
export APP_URL=https://你的站点域名
export CALLBACK_URL=https://你的站点域名/api/auth/callback

# 3. 用 uvicorn 或 gunicorn 启动
nohup uvicorn app:app --host 0.0.0.0 --port 8000 > tdstation.log 2>&1 &

# 4. Nginx 反代 + HTTPS（推荐）
# server { server_name 你的域名; location / { proxy_pass http://127.0.0.1:8000; proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; } }
```

## 接入 TokenPay 参赛（必做）

1. 官网登记报名（6 步流程：登记 → 入群 → 申请 TokenPay → 开通接入 → 打磨 → 推广）
2. 申请 TokenPay 时填写的 **App URL 必须与本项目 `APP_URL` 完全一致**（如 `https://你的站点域名`），且唯一稳定、不含敏感信息
3. 后台「复制给 AI」生成的接入信息与本项目实现一致：OAuth 授权页 + `X-App-URL` 归因 + `/gateway/v1` 网关
4. 用 20 元测试额度联调归因：授权 → 对话/图片/视频 → 后台核对调用量、用户数、消耗、收益
5. 推广：B 站视频引流、工具站互链，拉真实用户产生调用

## 目录结构

```
tdstation/
├── app.py            # FastAPI 后端（OAuth / 转发 / 归因 / 钱包）
├── requirements.txt
├── static/
│   ├── index.html    # 单页前端（对话/图片/视频/钱包/模型）
│   └── auth_ok.html  # 授权成功页
└── data/             # 服务端持久化用户 Key（运行时生成，勿外泄）
```

## 注意

- API Key 只存服务端 `data/keys.json`，禁止写入前端 / 日志 / Git
- 生产环境建议换用数据库或密钥管理存储 Key
- 视频任务视频 URL 有效期 24 小时
*（内容由AI生成，仅供参考）*
