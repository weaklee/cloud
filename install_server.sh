#!/bin/bash
set -uo pipefail

# ============================================================
# cloud Host Server 一键安装脚本
# 用法: bash install_server.sh
# 在宿主服务器上以 root 执行
# ============================================================

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()   { echo -e "${RED}[ERR]${NC} $1"; }

trap 'warn "上一条命令执行失败, 将继续执行"' ERR

[[ $EUID -eq 0 ]] || { err "请以 root 用户执行"; exit 1; }

DATA_DIR="/data/cloud"
IMAGES_DIR="$DATA_DIR/images"
DISKS_DIR="$DATA_DIR/disks"
VMS_DIR="$DATA_DIR/vms"
KERNEL_DIR="$DATA_DIR/kernel"
CLOUD_CTL="/usr/local/bin/cloud-ctl"
BRIDGE="cloud-br0"
BRIDGE_NET="10.100.0.1/24"
BRIDGE_CIDR="10.100.0.0/24"

# ---- 1. 检测发行版 ----
distro=""
if grep -qi debian /etc/os-release 2>/dev/null; then
    distro="debian"
elif grep -qi ubuntu /etc/os-release 2>/dev/null; then
    distro="ubuntu"
else
    err "仅支持 Debian / Ubuntu"
    exit 1
fi
info "检测到发行版: $distro"

# ---- 2. 安装系统依赖 ----
info "安装系统依赖..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    python3 \
    qemu-system-x86 \
    qemu-utils \
    iptables \
    bridge-utils \
    libguestfs-tools \
    genisoimage \
    wget \
    curl \
    xz-utils \
    dmidecode \
    socat || warn "部分 apt 包安装失败, 请检查上方输出"

# cloud-hypervisor (apt 源中可能没有, 从 GitHub 下载静态二进制)
CH_VER="v41.0"
CH_URL="https://github.com/cloud-hypervisor/cloud-hypervisor/releases/download/$CH_VER"
if ! command -v cloud-hypervisor &>/dev/null; then
    info "下载 cloud-hypervisor ($CH_VER) ..."
    wget -q "$CH_URL/cloud-hypervisor-static" -O /usr/local/bin/cloud-hypervisor 2>/dev/null && chmod +x /usr/local/bin/cloud-hypervisor || warn "cloud-hypervisor 下载失败 (可用 QEMU 替代)"
    wget -q "$CH_URL/ch-remote-static" -O /usr/local/bin/ch-remote 2>/dev/null && chmod +x /usr/local/bin/ch-remote || true
    if command -v cloud-hypervisor &>/dev/null; then
        info "cloud-hypervisor 安装成功"
    else
        warn "cloud-hypervisor 下载失败, 将使用 QEMU (不影响基本功能)"
    fi
else
    info "cloud-hypervisor 已安装"
fi

# ---- 3. 创建数据目录 ----
info "创建数据目录..."
mkdir -p "$IMAGES_DIR" "$DISKS_DIR" "$VMS_DIR" "$KERNEL_DIR"

# ---- 4. 网络: 桥接 + NAT ----
info "配置网络桥接 $BRIDGE ..."
if ! ip link show "$BRIDGE" &>/dev/null; then
    ip link add name "$BRIDGE" type bridge
    ip addr add "$BRIDGE_NET" dev "$BRIDGE"
    ip link set "$BRIDGE" up
    iptables -t nat -C POSTROUTING -s "$BRIDGE_CIDR" -j MASQUERADE 2>/dev/null ||
        iptables -t nat -A POSTROUTING -s "$BRIDGE_CIDR" -j MASQUERADE
    sysctl -w net.ipv4.ip_forward=1 >/dev/null
    grep -q 'net.ipv4.ip_forward' /etc/sysctl.conf 2>/dev/null ||
        echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf
    info "网桥 $BRIDGE 创建完成 ($BRIDGE_NET)"
else
    info "网桥 $BRIDGE 已存在"
fi

# ---- 5. 安装 cloud-ctl ----
info "安装 cloud-ctl -> $CLOUD_CTL"

cat > "$CLOUD_CTL" << 'CLOUDCTL_EOF'
#!/usr/bin/env python3
"""cloud-ctl — cloud VM management tool."""
import argparse, fcntl, json, os, random, shlex, shutil, signal, socket, struct
import subprocess, sys, time, uuid
from pathlib import Path

DATA_DIR = "/data/cloud"
IMAGES_DIR = os.path.join(DATA_DIR, "images")
DISKS_DIR = os.path.join(DATA_DIR, "disks")
VMS_DIR = os.path.join(DATA_DIR, "vms")
KERNEL_DIR = os.path.join(DATA_DIR, "kernel")
BRIDGE = "cloud-br0"

QEMU = shutil.which("qemu-system-x86_64") or shutil.which("qemu-system-x86")
CH = shutil.which("cloud-hypervisor")
CH_REMOTE = shutil.which("ch-remote")

IMAGE_URLS = {
    "debian-12": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2",
    "debian-11": "https://cloud.debian.org/images/cloud/bullseye/latest/debian-11-genericcloud-amd64.qcow2",
    "ubuntu-24.04": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
    "ubuntu-22.04": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
}

def log(msg): print(msg, file=sys.stderr)
def die(msg): log(f"error: {msg}"); sys.exit(1)
def json_out(data): print(json.dumps(data))

def _vm_cfg(vm_id): return os.path.join(VMS_DIR, f"{vm_id}.json")
def _disk(vm_id): return os.path.join(DISKS_DIR, f"{vm_id}.qcow2")
def _ci(vm_id): return os.path.join(DISKS_DIR, f"{vm_id}-cloudinit.iso")
def _tap(vm_id): return f"tap-{vm_id[:8]}"
def _mac(): return f"52:54:00:{random.randint(0x10,0xff):02x}:{random.randint(0x00,0xff):02x}:{random.randint(0x00,0xff):02x}"
def _sock(vm_id): return f"/tmp/vm-{vm_id}.sock"
def _pidfile(vm_id): return f"/tmp/vm-{vm_id}.pid"

def _ip_from_mac(mac):
    h = mac.replace(":", ""); v = int(h[-4:], 16)
    ip = (10 << 24) + (100 << 16) + (0 << 8) + (10 + (v % 245))
    return f"{ip>>24&255}.{ip>>16&255}.{ip>>8&255}.{ip&255}"

def _run(cmd, **kw):
    log(f"+ {' '.join(shlex.quote(str(a)) for a in cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def _is_running(vm_id):
    sock = _sock(vm_id)
    if not os.path.exists(sock): return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(sock)
        s.sendall(b'{"execute":"qmp_capabilities"}\n{"execute":"query-status"}\n')
        resp = s.recv(4096)
        s.close()
        return True
    except Exception:
        return False

def _qmp_cmd(vm_id, cmd):
    sock = _sock(vm_id)
    if not os.path.exists(sock): return
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(sock)
        s.sendall(cmd.encode() if isinstance(cmd, str) else cmd)
        s.close()
    except Exception:
        pass

def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0)); return s.getsockname()[1]

# ---- image subcommands ----
def cmd_image_list(args):
    images = []
    for fname in sorted(os.listdir(IMAGES_DIR)):
        fpath = os.path.join(IMAGES_DIR, fname)
        if fname.endswith(".qcow2") or fname.endswith(".img"):
            images.append({"name": fname.rsplit(".", 1)[0], "file": fname, "size_bytes": os.path.getsize(fpath)})
    json_out(images)

def cmd_image_download(args):
    name = args.name; url = IMAGE_URLS.get(name)
    if not url: die(f"未知镜像: {name}, 可选: {', '.join(IMAGE_URLS)}")
    dest = os.path.join(IMAGES_DIR, f"{name}.qcow2")
    if os.path.exists(dest):
        log(f"镜像 {name} 已存在"); json_out({"name": name, "file": f"{name}.qcow2", "cached": True}); return
    log(f"下载 {name} <- {url}")
    r = _run(["wget", "-q", "--show-progress", "-O", dest, url], timeout=600)
    if r.returncode != 0:
        try: os.remove(dest)
        except FileNotFoundError: pass
        die(f"下载失败: {r.stderr.strip()}")
    json_out({"name": name, "file": f"{name}.qcow2", "cached": False})

# ---- list ----
def cmd_list(args):
    vms = {}
    for fname in os.listdir(VMS_DIR):
        if not fname.endswith(".json"): continue
        vm_id = fname[:-5]
        try:
            with open(os.path.join(VMS_DIR, fname)) as f: cfg = json.load(f)
        except Exception: continue
        cfg["running"] = _is_running(vm_id); cfg["vm_id"] = vm_id
        vms[vm_id] = cfg
    json_out(vms)

# ---- create ----
def cmd_create(args):
    vm_id, name, cpus, mem_mb, disk_gb, image = args.id, args.name, args.cpus, args.memory, args.disk, args.image
    cfg_path, disk_path, ci_path = _vm_cfg(vm_id), _disk(vm_id), _ci(vm_id)
    if os.path.exists(cfg_path): die(f"VM {vm_id} 已存在")
    base_img = next((os.path.join(IMAGES_DIR, f"{image}{e}") for e in [".qcow2", ".img"] if os.path.exists(os.path.join(IMAGES_DIR, f"{image}{e}"))), None)
    if not base_img: die(f"镜像 '{image}' 未找到, 请先运行: cloud-ctl image download {image}")
    log(f"创建磁盘 {disk_path} ({disk_gb}G)")
    r = _run(["qemu-img", "create", "-f", "qcow2", "-b", base_img, "-F", "qcow2", disk_path, f"{disk_gb}G"])
    if r.returncode != 0: die(f"创建磁盘失败: {r.stderr.strip()}")
    mac = _mac(); ip = _ip_from_mac(mac)
    pw_hash = subprocess.run(["python3", "-c", "import crypt; print(crypt.crypt('cloud', crypt.gensalt('sha512')))"], capture_output=True, text=True).stdout.strip()
    seed = f"/tmp/cloudinit-{vm_id}"; os.makedirs(f"{seed}/seed")
    with open(f"{seed}/seed/meta-data", "w") as f: f.write(f"instance-id: {vm_id}\nlocal-hostname: {name}\n")
    with open(f"{seed}/seed/user-data", "w") as f:
        f.write(f"""#cloud-config
hostname: {name}
manage_etc_hosts: true
ssh_pwauth: true
disable_root: false
chpasswd:
  expire: false
  list:
  - root:{pw_hash}
users:
  - name: root
    lock_passwd: false
""")
    with open(f"{seed}/seed/network-config", "w") as f:
        f.write(f"""version: 2
ethernets:
  ens4:
    dhcp4: false
    addresses: [{ip}/24]
    gateway4: 10.100.0.1
    nameservers:
      addresses: [8.8.8.8, 1.1.1.1]
""")
    r = _run(["genisoimage", "-o", ci_path, "-input-charset", "utf-8", "-joliet", "-rock", f"{seed}/seed"])
    _run(["rm", "-rf", seed])
    if r.returncode != 0: _run(["rm", "-f", disk_path]); die(f"创建 cloud-init ISO 失败: {r.stderr.strip()}")
    cfg = {"name": name, "cpus": cpus, "memory_mb": mem_mb, "disk_gb": disk_gb, "image": image, "mac": mac, "ip": ip, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(cfg_path, "w") as f: json.dump(cfg, f)
    json_out({"vm_id": vm_id, "name": name, "ip": ip, "mac": mac, "disk_gb": disk_gb, "image": image})

# ---- start ----
def cmd_start(args):
    vm_id = args.id; cfg_path, disk_path, ci_path = _vm_cfg(vm_id), _disk(vm_id), _ci(vm_id)
    if not os.path.exists(cfg_path): die(f"VM {vm_id} 不存在")
    if _is_running(vm_id): log(f"VM {vm_id} 已在运行"); return
    with open(cfg_path) as f: cfg = json.load(f)
    tap = _tap(vm_id); mac = cfg["mac"]; ip = cfg["ip"]; mem = f"{cfg['memory_mb']}M"
    if not os.path.exists(f"/sys/class/net/{tap}"):
        _run(["ip", "tuntap", "add", tap, "mode", "tap"])
        _run(["ip", "link", "set", tap, "master", BRIDGE])
        _run(["ip", "link", "set", tap, "up"])
    ssh_port = _free_port()
    _run(["iptables", "-t", "nat", "-C", "PREROUTING", "-i", "eth0", "-p", "tcp", "--dport", str(ssh_port), "-j", "DNAT", "--to-destination", f"{ip}:22"], check=False)
    _run(["iptables", "-t", "nat", "-A", "PREROUTING", "-i", "eth0", "-p", "tcp", "--dport", str(ssh_port), "-j", "DNAT", "--to-destination", f"{ip}:22"], check=False)
    if QEMU:
        cmd = [QEMU, "-machine", "q35,accel=kvm", "-cpu", "host", "-smp", str(cfg["cpus"]),
               "-m", mem, "-nographic",
               "-drive", f"file={disk_path},format=qcow2,if=virtio",
               "-drive", f"file={ci_path},format=raw,if=virtio",
               "-netdev", f"tap,id=net0,ifname={tap},script=no,downscript=no",
               "-device", f"virtio-net-pci,netdev=net0,mac={mac}",
               "-device", "virtio-rng-pci",
               "-qmp", f"unix:{_sock(vm_id)},server=on,wait=off",
               "-pidfile", _pidfile(vm_id)]
        log(f"启动 QEMU VM {vm_id} ...")
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif CH:
        kernel, initrd = os.path.join(KERNEL_DIR, "vmlinux"), os.path.join(KERNEL_DIR, "initrd.img")
        if not os.path.exists(kernel) or not os.path.exists(initrd):
            die("cloud-hypervisor 需要内核文件, 请将 vmlinux 和 initrd.img 放入 {KERNEL_DIR}")
        cmd = [CH, "--api-socket", _sock(vm_id), "--kernel", kernel, "--initramfs", initrd,
               "--disk", f"path={disk_path}", "--disk", f"path={ci_path}",
               "--cpus", f"boot={cfg['cpus']}", "--memory", f"size={mem}",
               "--net", f"tap={tap},mac={mac}", "--console", "off", "--serial", "tty", "--rng"]
        log(f"启动 cloud-hypervisor VM {vm_id} ...")
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        die("未找到 QEMU 或 cloud-hypervisor")
    cfg["ssh_port"] = ssh_port; cfg["tap"] = tap
    with open(cfg_path, "w") as f: json.dump(cfg, f)
    log(f"VM {vm_id} 已启动, SSH端口: {ssh_port}, 内部IP: {ip}")

# ---- stop ----
def cmd_stop(args):
    vm_id = args.id
    if _is_running(vm_id):
        log(f"发送关机信号 VM {vm_id} ...")
        _qmp_cmd(vm_id, b'{"execute":"qmp_capabilities"}\n{"execute":"system_powerdown"}\n')
        for _ in range(10):
            time.sleep(1)
            if not _is_running(vm_id): break
    pidfile = _pidfile(vm_id)
    if os.path.exists(pidfile):
        try:
            with open(pidfile) as f: pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM); time.sleep(1)
        except (ProcessLookupError, ValueError, OSError): pass
        try: os.unlink(pidfile)
        except FileNotFoundError: pass
    for s in [_sock(vm_id)]:
        try: os.unlink(s)
        except FileNotFoundError: pass
    cfg_path = _vm_cfg(vm_id)
    if os.path.exists(cfg_path):
        with open(cfg_path) as f: cfg = json.load(f)
        sp, ip = cfg.get("ssh_port"), cfg.get("ip")
        if sp and ip:
            _run(["iptables", "-t", "nat", "-D", "PREROUTING", "-i", "eth0", "-p", "tcp", "--dport", str(sp), "-j", "DNAT", "--to-destination", f"{ip}:22"], check=False)
    tap = _tap(vm_id)
    _run(["ip", "link", "delete", tap], check=False)
    log(f"VM {vm_id} 已停止")

# ---- destroy ----
def cmd_destroy(args):
    vm_id = args.id
    if not os.path.exists(_vm_cfg(vm_id)): die(f"VM {vm_id} 不存在")
    cmd_stop(args)
    for p in [_disk(vm_id), _ci(vm_id), _vm_cfg(vm_id)]:
        try: os.remove(p); log(f"删除 {p}")
        except FileNotFoundError: pass
    log(f"VM {vm_id} 已销毁")

# ---- restart ----
def cmd_restart(args):
    cmd_stop(args); time.sleep(1); cmd_start(args)

# ---- reset-password ----
def cmd_reset_password(args):
    vm_id, pw = args.id, args.password
    disk = _disk(vm_id)
    if not os.path.exists(disk): die(f"VM {vm_id} 磁盘不存在")
    log(f"重置 VM {vm_id} root 密码...")
    r = _run(["virt-customize", "-a", disk, "--root-password", f"password:{pw}"], check=False)
    if r.returncode != 0:
        die(f"virt-customize 失败: {r.stderr.strip()}\n请确保安装了 libguestfs-tools")
    log(f"VM {vm_id} 密码已重置")

# ---- reinstall ----
def cmd_reinstall(args):
    vm_id, image = args.id, args.image
    cfg_path = _vm_cfg(vm_id)
    if not os.path.exists(cfg_path): die(f"VM {vm_id} 不存在")
    with open(cfg_path) as f: cfg = json.load(f)
    new_image = image or cfg.get("image", "debian-12")
    if _is_running(vm_id): log(f"VM {vm_id} 正在运行, 先停止"); cmd_stop(args)
    for p in [_disk(vm_id), _ci(vm_id)]:
        try: os.remove(p)
        except FileNotFoundError: pass
    args.image = new_image
    cmd_create(args)
    log(f"VM {vm_id} 已重装 ({new_image})")

# ---- main ----
def main():
    p = argparse.ArgumentParser(description="cloud VM management tool")
    s = p.add_subparsers(dest="subcommand", required=True)

    def _json(sp):
        sp.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
        return sp

    for name, help in [("list","列出所有VM"),("start","启动VM"),("stop","停止VM"),("destroy","销毁VM"),("restart","重启VM")]:
        sp = _json(s.add_parser(name, help=help))
        sp.add_argument("id", nargs="?" if name=="list" else None, default=None)
        sp.set_defaults(func=globals()[f"cmd_{name}"])
    pc = _json(s.add_parser("create", help="创建VM"))
    for a in [("--id",True),("--name",True),("--cpus",int,1),("--memory",int,512),("--disk",int,5),("--image",str,"debian-12")]:
        pc.add_argument(a[0], required=a[1] if isinstance(a[1],bool) else False, type=a[1] if isinstance(a[1],type) else str, default=a[2] if len(a)>2 else None)
    pc.set_defaults(func=cmd_create)
    pr = _json(s.add_parser("reset-password", help="重置密码"))
    pr.add_argument("id"); pr.add_argument("password"); pr.set_defaults(func=cmd_reset_password)
    pr2 = _json(s.add_parser("reinstall", help="重装系统"))
    pr2.add_argument("id"); pr2.add_argument("--image", default=""); pr2.set_defaults(func=cmd_reinstall)
    pi = s.add_parser("image", help="镜像管理")
    pis = pi.add_subparsers(dest="image_sub", required=True)
    _json(pis.add_parser("list")).set_defaults(func=cmd_image_list)
    pid = _json(pis.add_parser("download")); pid.add_argument("name"); pid.set_defaults(func=cmd_image_download)
    a = p.parse_args()
    try: a.func(a)
    except Exception as e: die(f"{e}")

if __name__ == "__main__": main()
CLOUDCTL_EOF

chmod +x "$CLOUD_CTL"
info "cloud-ctl 安装完成"

# ---- 6. 复制内核(cloud-hypervisor 备用) ----
info "准备内核文件 (cloud-hypervisor 备用)..."
if [ ! -f "$KERNEL_DIR/vmlinux" ] && [ -f "/boot/vmlinuz-$(uname -r)" ]; then
    cp "/boot/vmlinuz-$(uname -r)" "$KERNEL_DIR/vmlinux"
    cp "/boot/initrd.img-$(uname -r)" "$KERNEL_DIR/initrd.img" 2>/dev/null || true
    info "已复制当前系统内核"
fi

# ---- 7. 预下载默认镜像 ----
if [ ! -f "$IMAGES_DIR/debian-12.qcow2" ]; then
    info "预下载默认镜像 debian-12 (首次约 300MB)..."
    "$CLOUD_CTL" image download debian-12 || warn "下载失败, 稍后可手动运行: cloud-ctl image download debian-12"
fi

# ---- 8. 验证 ----
info "验证安装..."
"$CLOUD_CTL" image list || true
echo ""
echo "========================================"
echo " 安装完成!"
echo "========================================"
echo ""
echo "数据目录: $DATA_DIR"
echo "管理工具: cloud-ctl"
echo ""
echo "快速上手:"
echo "  cloud-ctl image list                    # 查看可用镜像"
echo "  cloud-ctl image download ubuntu-24.04   # 下载更多镜像"
echo "  cloud-ctl create --id myvm1 --name test --cpus 2 --memory 2048 --disk 10 --image debian-12"
echo "  cloud-ctl start myvm1"
echo "  cloud-ctl list"
echo "  cloud-ctl stop myvm1"
echo "  cloud-ctl destroy myvm1"
echo ""
echo "现在可以到面板 Admin > Servers 添加本机并点击「检测」验证连接"
