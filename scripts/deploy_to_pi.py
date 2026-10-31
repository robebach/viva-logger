#!/usr/bin/env python3
"""Automated remote setup and deployment to the newly imaged Raspberry Pi.

This script:
1. Waits for penelope-pi.local to finish initial boot and respond to SSH.
2. Connects using the initial password 'penelope'.
3. Installs git, python3-pip, python3-venv, and prerequisites.
4. Downloads and installs the LabJack LJM ARM64 driver.
5. Clones viva-logger, runs poetry install.
6. Installs and starts the systemd background service.
"""

import sys
import time
import subprocess
import pexpect

HOST = "penelope-pi.local"
USER = "pi"
PASSWORD = "penelope"


def run_remote_command(child, cmd, timeout=300):
    """Send a command over an active pexpect SSH session and wait for prompt."""
    print(f"\n---> Running: {cmd}", flush=True)
    child.sendline(cmd)
    prompt_regex = r"(pi@penelope-pi|beach@penelope-pi|[$#])\s*$"
    
    # We might encounter sudo password prompts
    while True:
        idx = child.expect([
            prompt_regex,
            r"\[sudo\] password for .*: ",
            r"Password: ",
            pexpect.TIMEOUT,
            pexpect.EOF,
        ], timeout=timeout)
        
        if idx == 0:
            # Reached command prompt, done
            break
        elif idx in (1, 2):
            print("  [Auto-filling sudo password]")
            child.sendline(PASSWORD)
        elif idx == 3:
            print(f"  [Timeout waiting for: {cmd}]")
            break
        elif idx == 4:
            print("  [SSH connection closed]")
            break


def main():
    print(f"Waiting for {HOST} to come online...", flush=True)
    
    # Poll until SSH port responds
    connected = False
    start_time = time.time()
    while time.time() - start_time < 300:
        res = subprocess.run(
            ["nc", "-zv", "-w", "2", HOST, "22"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if res.returncode == 0:
            print(f"Found {HOST} SSH port open!", flush=True)
            connected = True
            break
        time.sleep(3)
        print(".", end="", flush=True)

    if not connected:
        print(f"\nCould not connect to {HOST} after 5 minutes. Check Wi-Fi.", file=sys.stderr)
        sys.exit(1)

    print(f"\nConnecting to {USER}@{HOST} with SSH...", flush=True)
    ssh_cmd = (
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"{USER}@{HOST}"
    )
    
    child = pexpect.spawn(ssh_cmd, encoding="utf-8", timeout=30)
    idx = child.expect([r"[P|p]assword:", r"Are you sure you want to continue", pexpect.TIMEOUT])
    if idx == 1:
        child.sendline("yes")
        child.expect(r"[P|p]assword:")
    elif idx == 2:
        print("SSH timed out waiting for password prompt.")
        sys.exit(1)

    child.sendline(PASSWORD)
    child.expect(r"(pi@penelope-pi|[$#])\s*")
    print("Logged in successfully!", flush=True)

    # 1. Update system packages and install git, python tools, unzip
    run_remote_command(child, "sudo apt-get update")
    run_remote_command(child, "sudo apt-get install -y git python3-pip python3-venv python3-dev unzip curl libusb-1.0-0")

    # 2. Download and install LabJack LJM ARM64
    print("\nInstalling LabJack LJM driver...", flush=True)
    run_remote_command(child, "mkdir -p ~/labjack_install && cd ~/labjack_install")
    # Fetch latest AArch64 release
    run_remote_command(
        child,
        "cd ~/labjack_install && curl -fsSL -O https://files.labjack.com/installers/LJM/Linux/AArch64/LabJack-LJM_2023-04-04_arm64.tar.gz || "
        "curl -fsSL -O https://labjack.com/sites/default/files/software/LabJack-LJM_2023-04-04_arm64.tar.gz"
    )
    run_remote_command(child, "cd ~/labjack_install && tar -xzf LabJack-LJM_*_arm64.tar.gz")
    run_remote_command(child, "cd ~/labjack_install/LabJack-LJM_* && sudo ./labjack_ljm_installer.run -- --without-kipling")

    # 3. Install poetry
    print("\nInstalling Poetry...", flush=True)
    run_remote_command(child, "curl -sSL https://install.python-poetry.org | python3 -")
    run_remote_command(child, "export PATH=\"$HOME/.local/bin:$PATH\"")

    # 4. Clone / pull repository
    print("\nSetting up viva-logger...", flush=True)
    run_remote_command(child, "mkdir -p ~/DEV")
    run_remote_command(child, "cd ~/DEV && (git clone https://github.com/robebach/viva-logger.git || (cd viva-logger && git pull origin main))")

    # 5. Poetry install
    run_remote_command(child, "cd ~/DEV/viva-logger && $HOME/.local/bin/poetry install")

    # 6. Install & enable systemd service
    print("\nConfiguring systemd service...", flush=True)
    run_remote_command(child, "cd ~/DEV/viva-logger && sudo cp systemd/viva-logger.service /etc/systemd/system/")
    run_remote_command(child, "sudo systemctl daemon-reload")
    run_remote_command(child, "sudo systemctl enable --now viva-logger")

    # 7. Check service status
    run_remote_command(child, "systemctl status viva-logger --no-pager")

    print("\n=== DEPLOYMENT COMPLETE! ===", flush=True)
    child.sendline("exit")
    child.close()


if __name__ == "__main__":
    main()
