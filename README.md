# BJTU 选课助手

北京交通大学自动抢课工具，基于 FastAPI + Vue 3 构建的 Web 应用，支持验证码自动识别、多课程并行抢选、按教师名筛选。

## 功能特点

- 自动登录 BJTU 教务系统（CAS 单点登录）
- ONNX 模型本地识别登录验证码，无需第三方服务
- 支持按**课程名称**或**教师姓名**筛选目标课程
- 支持必修课和任选课两种选课类型
- 支持高级课程筛选
- 图鉴 API 自动识别选课提交验证码
- 实时日志面板，抢课状态一目了然
- WebSocket 实时通信，断线自动重连
- 多用户同时使用，互不干扰

## 技术栈

- **后端**：Python 3.8+ / FastAPI / Uvicorn
- **前端**：Vue 3（CDN）/ 单文件 HTML
- **通信**：WebSocket
- **验证码识别**：ONNX Runtime（本地）+ 图鉴 API（提交验证码）

## 快速开始

### 环境要求

- Python 3.8 及以上版本
- pip 包管理器

### 安装与运行

```bash
# 1. 克隆项目
git clone https://github.com/wangxiaobo1747/BJTU-course-autoget-program.git
cd BJTU-course-autoget-program

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务
python server.py
```

启动后在浏览器打开 **http://localhost:8765** 即可使用。

## 服务器部署（Linux）

### 1. 安装依赖

```bash
# CentOS / Alibaba Cloud Linux
sudo dnf install python3.11 python3.11-pip git -y

# Ubuntu / Debian
sudo apt update && sudo apt install python3 python3-pip python3-venv git -y
```

### 2. 部署项目

```bash
# 克隆项目
git clone https://github.com/wangxiaobo1747/BJTU-course-autoget-program.git /opt/qiangke
cd /opt/qiangke

# 创建虚拟环境并安装依赖
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置 systemd 服务

创建 `/etc/systemd/system/qiangke.service`：

```ini
[Unit]
Description=BJTU Course Selection Assistant
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/qiangke
ExecStart=/opt/qiangke/venv/bin/python /opt/qiangke/server.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable qiangke
sudo systemctl start qiangke
```

### 4. 配置 Nginx 反向代理

创建 Nginx 配置文件（路径根据实际安装位置调整）：

```nginx
server {
    listen 80;
    server_name your-domain.com;  # 替换为你的域名或IP

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }
}
```

重载 Nginx：

```bash
nginx -t && nginx -s reload
```

### 5. 配置 HTTPS（可选）

需要先配置好域名解析，然后使用 certbot 自动申请证书：

```bash
# 安装 certbot
sudo dnf install certbot python3-certbot-nginx -y   # CentOS
# sudo apt install certbot python3-certbot-nginx -y  # Ubuntu

# 申请并自动配置证书
sudo certbot --nginx -d your-domain.com
```

## 使用说明

1. 打开网页，选择课程类型（必修课 / 任选课）
2. 填写 MIS 学号和密码
3. 填写图鉴 API 账号和密码（用于识别选课提交验证码，[注册图鉴](https://www.ttshitu.com/)）
4. 在选课列表中输入目标课程名称或教师姓名，多个用英文逗号分隔
   - 示例：`大学美育,杨梦婉`（同时匹配课程名和教师名）
5. 如需抢高级课程，勾选"抢高级课"
6. 点击"开始抢课"，程序会自动登录并持续尝试选课

## 项目结构

```
├── server.py              # FastAPI 服务端（WebSocket）
├── index.html             # 前端页面（Vue 3 单文件）
├── requirements.txt       # Python 依赖
├── src/
│   ├── login.py           # 核心逻辑：登录、抢课、验证码
│   ├── omis.onnx          # 登录验证码识别模型
│   └── icon.ico           # 图标
├── python/
│   └── bjtupythonstub.py  # 原 Electron 版 WebSocket 脚本（保留参考）
├── main.js                # 原 Electron 主进程（保留参考）
└── preload.js             # 原 Electron 预加载脚本（保留参考）
```

## 注意事项

- 请确保输入的课程名称或教师姓名准确无误
- 图鉴 API 需要余额，请提前充值（[图鉴官网](https://www.ttshitu.com/)）
- 建议使用稳定的网络连接
- 抢课过程中可随时点击"停止"终止任务

## 免责声明

本工具仅供学习交流使用，请勿用于其他用途。使用本工具造成的任何问题由使用者自行承担。

## License

MIT
