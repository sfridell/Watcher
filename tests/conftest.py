import os
import tempfile

import pytest

from tests.qemu_manager import QEMUManager
from routers.ddwrt import DDWRTRouter
from routers.openwrt import OpenWrtRouter


@pytest.fixture(scope="session")
def ddwrt_vm():
    """Session-scoped fixture: boots DD-WRT VM, provisions SSH keys, yields (conn, handler), tears down."""
    mgr = QEMUManager()
    mgr._check_kvm()
    mgr.download_image("ddwrt_x86")
    mgr.convert_to_qcow2("ddwrt_x86")
    mgr.create_overlay("ddwrt_x86")
    proc = mgr.start_vm("ddwrt_x86")
    try:
        mgr.wait_for_ssh("localhost", 2222, router_name="ddwrt_x86", timeout=180)
        mgr.provision_keys("ddwrt_test", "localhost", "2222", "root", "admin")
        conn = mgr.get_connection("ddwrt_test", "localhost", "2222")
        handler = DDWRTRouter()
        yield conn, handler
    finally:
        mgr.stop_vm(proc)
        mgr.cleanup("ddwrt_x86")


@pytest.fixture(scope="session")
def openwrt_vm():
    """Session-scoped fixture: boots OpenWrt VM.

    OpenWrt 23.05 x86/64 generic ships without busybox-telnetd, but the
    first-boot ttyS0 getty runs a no-password BusyBox shell, so the fixture
    exposes the guest serial port to a Unix socket, drives the getty to
    set the root password, then provisions the SSH key via SSH password
    login. Yields (conn, handler), tears down.
    """
    serial_sock = os.path.join(tempfile.gettempdir(), "openwrt_qemu_serial.sock")
    if os.path.exists(serial_sock):
        os.unlink(serial_sock)

    mgr = QEMUManager()
    mgr._check_kvm()
    mgr.download_image("openwrt_x86")
    mgr.convert_to_qcow2("openwrt_x86")
    mgr.create_overlay("openwrt_x86")
    proc = mgr.start_vm("openwrt_x86", serial_sock=serial_sock)
    try:
        mgr.wait_for_ssh("localhost", 2223, router_name="openwrt_x86", timeout=180)
        mgr.serial_set_password(serial_sock, "admin")
        mgr.provision_keys(
            "openwrt_test", "localhost", "2223", "root", "admin",
            router_type="openwrt",
        )
        conn = mgr.get_connection("openwrt_test", "localhost", "2223")
        handler = OpenWrtRouter()
        yield conn, handler
    finally:
        mgr.stop_vm(proc)
        mgr.cleanup("openwrt_x86")
        if os.path.exists(serial_sock):
            os.unlink(serial_sock)


def pytest_addoption(parser):
    parser.addoption(
        "--skip-qemu",
        action="store_true",
        default=False,
        help="Skip QEMU VM integration tests",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--skip-qemu"):
        skip_qemu = pytest.mark.skip(reason="--skip-qemu specified: skipping QEMU VM tests")
        for item in items:
            if "ddwrt_vm" in item.fixturenames or "openwrt_vm" in item.fixturenames:
                item.add_marker(skip_qemu)