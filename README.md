## 快速开始

### 1. 安装面板

```bash
git clone https://github.com/weaklee/cloud.git
cd cloud
pip install -r requirements.txt
python run.py
```

访问 `http://localhost:3000`，默认管理员：`admin` / `admin123`

### 2. 安装宿主服务器

```bash
# 在宿主服务器上运行（需要 root）
chmod +x install_server.sh
bash install_server.sh
```

自动安装：cloud-hypervisor、Incus、网络桥接、SSH 配置

### 3. 添加服务器

1. 管理员登录 → 宿主服务器 → 添加服务器
2. 输入 IP、SSH 端口、用户名、密码
3. 点击"检测"验证连接

### 4. 创建云主机

1. 用户登录 → 我的云主机 → 创建云主机
2. 选择虚拟化类型（KVM / Incus）
3. 选择宿主服务器
4. 配置 CPU / 内存 / 磁盘
5. 选择操作系统镜像
6. （Incus）选择 IPv6 地址
7. 立即创建

---

## 项目结构

```
v1.0/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── models.py             # 数据模型（User/Server/VM/PortForward...）
│   ├── database.py           # 异步数据库 + 自动迁移
│   ├── auth.py               # JWT 认证
│   ├── config.py             # 配置
│   ├── routers/
│   │   ├── auth_router.py    # 登录/注册
│   │   ├── user_router.py    # 用户端全部路由
│   │   └── admin_router.py   # 管理端全部路由
│   ├── services/
│   │   ├── ssh_client.py     # SSH 连接管理（Paramiko + asyncio）
│   │   └── vm_manager.py     # VM/Incus 操作封装
│   └── templates/            # Jinja2 模板
│       ├── base.html         # 布局 + 侧边栏
│       ├── _macros.html      # 公共组件
│       ├── user/             # 用户端页面
│       └── admin/            # 管理端页面
├── install_host.py           # 宿主服务器一键安装脚本
└── run.py                    # 启动脚本
```
## Roadmap

- [ ] VNC Web 控制台（noVNC）
- [ ] 镜像仓库管理（上传/下载/预置）
- [ ] WebSocket 实时状态推送
- [ ] 流量统计与限速
- [ ] 自动备份与快照
- [ ] API Token 认证（RESTful）
- [ ] 多语言支持

