import gzip
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from fabric import Connection


class QEMUManager:
    """Manages QEMU virtual router VMs for integration testing.

    Handles the full lifecycle: download disk images, convert to QEMU format,
    create overlay images for test isolation, start/stop VMs, and provision
    SSH key-based authentication.
    """

    def __init__(self, config_path="tests/qemu_config.json"):
        self.config = self._load_config(config_path)
        self.images_dir = Path(self.config["images_dir"])
        self._vms = {}

    def _load_config(self, config_path):
        with open(config_path, "r") as f:
            return json.load(f)

    def _check_kvm(self):
        if not os.path.exists("/dev/kvm"):
            raise RuntimeError(
                "KVM not available. /dev/kvm not found. "
                "Enable hardware virtualization in BIOS and install qemu-kvm."
            )

    def _image_paths(self, router_name):
        router_cfg = self.config["routers"][router_name]
        base_name = router_cfg.get("base_name", router_name)

        if "local_image" in router_cfg:
            local = Path(router_cfg["local_image"])
            downloaded = local
            base_qcow2 = self.images_dir / f"{base_name}.qcow2"
        else:
            downloaded = self.images_dir / f"{base_name}.image"
            base_qcow2 = self.images_dir / f"{base_name}.qcow2"

        overlay = self.images_dir / f"{base_name}_overlay.qcow2"
        return downloaded, base_qcow2, overlay

    def download_image(self, router_name):
        """Download/copy the router disk image. Skips if base qcow2 already exists.
        For local_image configs, copies the local qcow2 to images_dir as the base."""
        _, base_qcow2, _ = self._image_paths(router_name)
        if base_qcow2.exists():
            return base_qcow2

        router_cfg = self.config["routers"][router_name]
        self.images_dir.mkdir(parents=True, exist_ok=True)

        if "local_image" in router_cfg:
            local = Path(router_cfg["local_image"])
            if not local.exists():
                raise FileNotFoundError(f"Local image not found: {local}")
            print(f"Copying local image {local} to {base_qcow2}...")
            shutil.copy2(str(local), str(base_qcow2))
            return base_qcow2

        url = router_cfg["image_url"]
        compression = router_cfg.get("image_compression")
        downloaded, _, _ = self._image_paths(router_name)

        if not downloaded.exists():
            print(f"Downloading {url}...")
            subprocess.run(
                ["curl", "-L", "-o", str(downloaded), url],
                check=True,
            )

        if compression == "gzip":
            decompressed = downloaded.with_suffix("")
            if not decompressed.exists():
                print(f"Decompressing {downloaded}...")
                with gzip.open(downloaded, "rb") as f_in:
                    with open(decompressed, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)

        return downloaded.with_suffix("") if compression == "gzip" else downloaded

    def convert_to_qcow2(self, router_name):
        """Convert the raw disk image to QCOW2 format. Skips if base qcow2 already exists.
        For local_image configs, the copy is already qcow2, so this is a no-op."""
        _, base_qcow2, _ = self._image_paths(router_name)
        if base_qcow2.exists():
            return base_qcow2

        router_cfg = self.config["routers"][router_name]
        if "local_image" in router_cfg:
            return base_qcow2

        compression = router_cfg.get("image_compression")
        downloaded, _, _ = self._image_paths(router_name)

        if compression == "gzip":
            raw_image = downloaded.with_suffix("")
        else:
            raw_image = downloaded

        if not raw_image.exists():
            self.download_image(router_name)
            if compression == "gzip":
                raw_image = downloaded.with_suffix("")
            else:
                raw_image = downloaded

        print(f"Converting {raw_image} to QCOW2...")
        subprocess.run(
            ["qemu-img", "convert", "-O", "qcow2", str(raw_image), str(base_qcow2)],
            check=True,
        )
        return base_qcow2

    def create_overlay(self, router_name):
        """Create a QCOW2 overlay on top of the base image for test isolation."""
        _, base_qcow2, overlay = self._image_paths(router_name)

        if not base_qcow2.exists():
            self.convert_to_qcow2(router_name)

        if overlay.exists():
            overlay.unlink()

        abs_base = base_qcow2.resolve()
        abs_overlay = overlay.resolve()
        print(f"Creating overlay image {overlay}...")
        subprocess.run(
            [
                "qemu-img", "create", "-f", "qcow2",
                "-F", "qcow2",
                "-b", str(abs_base),
                str(abs_overlay),
            ],
            check=True,
        )
        return overlay

    def start_vm(self, router_name, serial_sock=None):
        """Start a QEMU VM with the overlay image and port forwarding. Returns the Popen process.

        If ``serial_sock`` is given, the guest's ttyS0 serial port is exposed
        as a QEMU Unix-socket server at that path (mode ``server,nowait``),
        so callers can drive the first-boot BusyBox getty interactively
        (used by OpenWrt 23.05 which ships no busybox-telnetd).
        """
        self._check_kvm()

        _, _, overlay = self._image_paths(router_name)
        abs_overlay = overlay.resolve()
        router_cfg = self.config["routers"][router_name]
        qemu_cfg = router_cfg["qemu"]

        hostfwd_args = []
        for fwd in qemu_cfg["hostfwd"]:
            hostfwd_args.extend([
                f"hostfwd={fwd['proto']}::{fwd['host_port']}-{qemu_cfg['guest_ip']}:{fwd['guest_port']}"
            ])

        netdev_arg = (
            f"user,id=lan0,net={qemu_cfg['net']}"
            + ("," + ",".join(hostfwd_args) if hostfwd_args else "")
        )

        cmd = [
            "qemu-system-x86_64",
            "-enable-kvm",
            "-m", str(qemu_cfg["memory"]),
            "-smp", str(qemu_cfg["smp"]),
            "-drive", f"file={abs_overlay},format=qcow2,if=ide",
            "-netdev", netdev_arg,
            "-device", f"{qemu_cfg['nic_model']},netdev=lan0,mac=52:54:00:12:34:57",
            "-display", "none",
            "-monitor", "stdio",
        ]
        if serial_sock:
            if os.path.exists(serial_sock):
                os.unlink(serial_sock)
            cmd.extend(["-serial", f"unix:{serial_sock},server,nowait"])

        print(f"Starting QEMU VM: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._vms[router_name] = proc
        return proc

    def wait_for_ssh(self, host, port, timeout=None, router_name=None):
        """Poll until SSH is available on the given host:port, with a timeout in seconds."""
        if timeout is None:
            if router_name:
                timeout = self.config["routers"][router_name]["ssh"]["timeout"]
            else:
                timeout = 120

        start = time.time()
        while time.time() - start < timeout:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                sock.connect((host, int(port)))
                data = sock.recv(256)
                sock.close()
                if b"SSH" in data:
                    print(f"SSH ready on {host}:{port} after {time.time() - start:.1f}s")
                    return True
            except Exception:
                pass
            time.sleep(2)

        raise TimeoutError(f"SSH not available on {host}:{port} within {timeout}s")

    def serial_set_password(self, serial_sock, password, timeout=60):
        """Set the root password on a fresh OpenWrt image via its first-boot
        ttyS0 serial console.

        OpenWrt runs a no-password BusyBox getty on ttyS0 on first boot
        (displaying "Please press Enter to activate this console."). This
        drives that getty over a QEMU Unix-socket serial port to run
        ``printf "<pw>\\n<pw>\\n" | passwd``, since OpenWrt 23.05 x86/64
        ships neither busybox-telnetd, chpasswd, nor mkpasswd, and dropbear
        rejects blank-password SSH logins by default. BusyBox's ``passwd``
        DOES accept passwords from stdin (after emitting "New password:"
        and "Retype password:" prompts).

        Call AFTER ``wait_for_ssh`` has confirmed the VM is fully booted
        (dropbear is the last service started by procd).
        """
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        deadline = time.time() + 30
        while not os.path.exists(serial_sock) and time.time() < deadline:
            time.sleep(0.2)
        if not os.path.exists(serial_sock):
            raise FileNotFoundError(
                f"Serial socket {serial_sock} never appeared; "
                "is the VM running with serial_sock= set?"
            )
        sock.connect(serial_sock)

        prompt = b":/#"

        def wait_for_prompt(deadline):
            buf = b""
            while prompt not in buf and time.time() < deadline:
                try:
                    chunk = sock.recv(4096)
                    if chunk:
                        buf += chunk
                except socket.timeout:
                    # If the shell is dormant, nudge it with an Enter.
                    sock.sendall(b"\n")
            return buf

        try:
            # Drain any pending boot output.
            sock.settimeout(1)
            try:
                while True:
                    if not sock.recv(65536):
                        break
            except socket.timeout:
                pass

            # Activate the getty (requires Enter) and wait for the BusyBox prompt.
            sock.settimeout(2)
            sock.sendall(b"\n")
            buf = wait_for_prompt(time.time() + timeout)
            if prompt not in buf:
                raise RuntimeError(
                    f"No BusyBox prompt on serial console {serial_sock} "
                    f"within {timeout}s (last buf: {buf[-200:]!r})"
                )

            # Drain any extra prompt echoes, then send the password-change
            # command. printf escapes \\n to actual newlines so BusyBox
            # passwd receives both the new and confirm passwords.
            sock.settimeout(0.5)
            try:
                while True:
                    if not sock.recv(65536):
                        break
            except socket.timeout:
                pass

            pw = password.encode()
            cmd = b'printf "' + pw + b'\\n' + pw + b'\\n" | passwd\n'
            sock.settimeout(2)
            sock.sendall(cmd)
            buf = wait_for_prompt(time.time() + 15)
            if prompt not in buf:
                print(f"WARNING: prompt not seen after passwd "
                      f"(buf: {buf[-200:]!r})")
        finally:
            try:
                sock.close()
            except Exception:
                pass
        print(f"Root password set via serial console at {serial_sock}")

    def provision_keys(self, name, host, port, username, password, router_type="ddwrt"):
        """Provision SSH key-based auth on the router using password login."""
        import connectiondb

        cdb = connectiondb.ConnectionDB()
        output_stream = __import__("io").StringIO()

        if name not in cdb.connections:
            cdb.connections[name] = {
                "ip": host,
                "port": str(port),
                "username": username,
                "router_type": router_type,
            }
            cdb._save_connections()

        cdb.provision_ssh_keys(name, host, str(port), username, password, output_stream,
                              router_type=router_type)
        print(output_stream.getvalue())

    def get_connection(self, name, host, port):
        """Create a Fabric Connection using the provisioned SSH key."""
        import paramiko
        return Connection(
            host=host, user="root", port=int(port),
            connect_kwargs={
                "pkey": paramiko.RSAKey.from_private_key_file(f"./keyfiles/{name}_rsa"),
                "disabled_algorithms": dict(pubkeys=["rsa-sha2-512", "rsa-sha2-256"]),
                "look_for_keys": False,
                "allow_agent": False,
            },
        )

    def stop_vm(self, proc, timeout=30):
        """Stop a QEMU VM by sending 'quit' to the monitor, then terminating if needed."""
        if proc.poll() is not None:
            return

        try:
            proc.stdin.write(b"quit\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)

    def cleanup(self, router_name):
        """Remove the overlay image file."""
        _, _, overlay = self._image_paths(router_name)
        if overlay.exists():
            overlay.unlink()
            print(f"Removed overlay {overlay}")