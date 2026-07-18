#!/bin/bash
set -e

echo "=== QEMU Router Integration Test Runner ==="

# Check for KVM
if [ ! -e /dev/kvm ]; then
    echo "ERROR: KVM not available (/dev/kvm not found)"
    echo "Enable hardware virtualization in BIOS and install qemu-kvm."
    exit 1
fi

# Check for required tools
for cmd in qemu-system-x86_64 qemu-img curl; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "ERROR: $cmd not found. Please install qemu-kvm and qemu-utils."
        exit 1
    fi
done

# Check for Python dependencies
python3 -c "import fabric; import pytest" 2>/dev/null || {
    echo "ERROR: Missing Python dependencies. Run: pip install -r requirements.txt"
    exit 1
}

# Use python3 -m pytest so we don't depend on pytest being on PATH
# (common in venv/pipx setups where only the module is importable).
PYTEST="python3 -m pytest"

echo "All prerequisites met. Starting tests..."
echo ""

# Allow selecting specific test files via positional args or TEST_FILES env var.
# If positional args are passed (e.g. ./run_qemu_tests.sh tests/test_openwrt_qemu.py)
# they take precedence; otherwise default to both router suites (or $TEST_FILES).
if [ $# -gt 0 ]; then
    TEST_FILES="$@"
elif [ -n "${TEST_FILES:-}" ]; then
    : # use TEST_FILES from env
else
    TEST_FILES="tests/test_ddwrt_qemu.py tests/test_openwrt_qemu.py"
fi

$PYTEST $TEST_FILES -v -s --timeout=300
exit_code=$?

echo ""
if [ $exit_code -eq 0 ]; then
    echo "=== All QEMU tests passed ==="
else
    echo "=== QEMU tests failed (exit code: $exit_code) ==="
fi

exit $exit_code