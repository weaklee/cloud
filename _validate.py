# -*- coding: utf-8 -*-
import sys, os, io, sqlite3
sys.path.insert(0, os.path.dirname(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from fastapi.testclient import TestClient
from app.main import app

passed, failed = [], []
def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  -> " + extra if extra and not cond else ""))

# 确保管理员存在(新库 cloud-panel.db)
import subprocess
subprocess.run([sys.executable, "seed.py"], capture_output=True)

DB = os.path.join("data", "cloud-panel.db")

with TestClient(app) as c:
    r = c.get("/login")
    check("登录页 200", r.status_code == 200)
    check("登录页品牌 cloud", "cloud" in r.text and "ikun" not in r.text)
    check("登录页浅色", "linear-gradient(135deg,#eef2ff" in r.text)

    c.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)

    r = c.get("/dashboard")
    check("控制台无 ikun", "ikun" not in r.text)
    check("控制台浅色", "--bg:#f4f6fb" in r.text)
    check("侧边栏品牌 cloud", "> cloud</div>" in r.text)

    # 添加服务器(密码字段)
    r = c.post("/admin/servers/create", data={
        "name": "测试节点", "host": "10.0.0.99", "port": "22",
        "ssh_user": "root", "ssh_password": "mypassword123"
    }, follow_redirects=False)
    check("添加服务器(密码) 302", r.status_code == 302, str(r.status_code))

    r = c.get("/admin/servers")
    check("服务器列表含节点", "测试节点" in r.text)
    check("服务器列表无 ikun", "ikun" not in r.text)

    r = c.get("/vms/create")
    check("创建页含服务器", "10.0.0.99" in r.text)
    check("创建页无 ikun", "ikun" not in r.text)

    # 检测服务器:连不上应给中文提示而非崩溃
    r = c.post("/admin/servers/1/check", follow_redirects=False)
    check("检测服务器不崩溃 302", r.status_code == 302, str(r.status_code))

    for p in ["/admin/dashboard", "/admin/users", "/admin/vms", "/admin/settings",
              "/admin/announcements", "/admin/logs", "/profile", "/vms"]:
        rr = c.get(p)
        check(f"{p} 200且无ikun", rr.status_code == 200 and "ikun" not in rr.text,
              f"{rr.status_code} ikun={'ikun' in rr.text}")

# 同步检查 DB 结构(新库应在 with 块外可读)
conn = sqlite3.connect(DB)
cols = {row[1] for row in conn.execute("PRAGMA table_info(servers)").fetchall()}
conn.close()
check("DB servers 含 ssh_password 列", "ssh_password" in cols, str(cols))
check("DB servers 不含 ssh_key 列", "ssh_key" not in cols, str(cols))

print("\n==== %d passed, %d failed ====" % (len(passed), len(failed)))
if failed:
    print("FAILED:", failed); sys.exit(1)
