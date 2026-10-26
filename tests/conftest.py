"""pytest 入口：与 unittest 共用同一套测试沙箱。"""

try:
    from tests._sandbox import install
except ImportError:  # pytest 以 tests/ 为 rootdir 时
    from _sandbox import install

install()
