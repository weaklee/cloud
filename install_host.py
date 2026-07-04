#!/usr/bin/env python3
"""
cloud Host Server Installer
Single-file script to install all dependencies on a VM host server (Server B).
Usage: python3 install_host.py
"""
import os
import sys
import subprocess


def run(cmd, check=True):
    print(f"  + {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"  ERROR: {r.stderr.strip()}")
        sys.exit(1)
    return r


def check_root():
    if os.geteuid() != 0:
        print("This script must be run as root.")
        sys.exit(1)


def get_distro():
    if os.path.exists("/etc/debian_version"):
        return "debian"
    return "unknown"


def install_dependencies():
    print("[1/6] Installing system dependencies...")
    run("apt-get update -qq")
    run("apt-get install -y -qq python3 python3-pip qemu-utils cloud-hypervisor iptables bridge-utils")


def install_incus():
    print("[2/6] Installing Incus...")
    r = run("which incus", check=False)
    if r.returncode == 0:
        print("  Incus already installed.")
        return
    print("  Adding Incus repository...")
    run("apt-get install -y -qq gpg wget")
    run("wget -q -O /etc/apt/keyrings/incus-archive-keyring.gpg https://packages.linuxcontainers.org/incus/archive.key",
        check=False)
    run('echo "deb [signed-by=/etc/apt/keyrings/incus-archive-keyring.gpg] https://packages.linuxcontainers.org/incus/ $(lsb_release -cs) main" > /etc/apt/sources.list.d/incus.list',
        check=False)
    run("apt-get update -qq", check=False)
    run("apt-get install -y -qq incus", check=False)
    r2 = run("which incus", check=False)
    if r2.returncode == 0:
        print("  Incus installed successfully.")
        run("incus admin init --auto", check=False)
    else:
        print("  Warning: Incus installation failed. Incus VMs will not be available.")


def setup_ssh_password():
    print("[3/6] Verifying SSH password login...")
    r = run("grep -q '^PasswordAuthentication yes' /etc/ssh/sshd_config", check=False)
    if r.returncode != 0:
        print("  Enabling SSH password authentication...")
        run("sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config")
        run("systemctl restart sshd || systemctl restart ssh")
        print("  PasswordAuthentication enabled.")
    else:
        print("  SSH password authentication already enabled.")


def setup_cloud_ctl():
    print("[4/6] Installing cloud-ctl...")
    ctl_path = "/usr/local/bin/cloud-ctl"
    if not os.path.exists(ctl_path):
        print("  cloud-ctl will be managed by the control panel via SSH.")
        print("  Ensure the cloud-ctl script is available on the host.")
    else:
        print("  cloud-ctl already installed.")


def setup_data_dirs():
    print("[5/6] Creating data directories...")
    dirs = ["/data/cloud/images", "/data/cloud/disks",
            "/data/cloud/vms", "/data/cloud/kernel"]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
        print(f"  Created {d}")


def setup_network():
    print("[6/6] Configuring network bridge...")
    r = run("ip link show cloud-br0", check=False)
    if r.returncode != 0:
        run("ip link add name cloud-br0 type bridge")
        run("ip addr add 10.100.0.1/24 dev cloud-br0")
        run("ip link set cloud-br0 up")
        run("iptables -t nat -A POSTROUTING -s 10.100.0.0/24 -j MASQUERADE")
        run("sysctl -w net.ipv4.ip_forward=1")
        print("  Bridge cloud-br0 created (10.100.0.1/24)")
    else:
        print("  Bridge cloud-br0 already exists.")


def print_summary():
    print("\n" + "=" * 60)
    print("Host server installation complete!")
    print("=" * 60)
    print("\nTo connect this host to your cloud panel:")
    print("1. In the panel, go to Admin > Servers > Add Server")
    print("2. Enter this server's IP, SSH port, SSH user (root) and SSH password")
    print("3. Click 'Check' to verify the connection")
    print("\nDefault data directory: /data/cloud")


if __name__ == "__main__":
    print("cloud Host Server Installer")
    print("=" * 60)
    check_root()
    get_distro()
    install_dependencies()
    install_incus()
    setup_ssh_password()
    setup_cloud_ctl()
    setup_data_dirs()
    setup_network()
    print_summary()
