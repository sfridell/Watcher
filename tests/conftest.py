import pytest

from tests.qemu_manager import QEMUManager
from routers.ddwrt import DDWRTRouter


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
            if "ddwrt_vm" in item.fixturenames:
                item.add_marker(skip_qemu)