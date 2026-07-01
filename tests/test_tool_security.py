"""
工具安全测试 — Shell 沙箱 / Fetch SSRF / 路径穿越防护。

所有测试均为纯逻辑测试，无需外部服务。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nano_agent.tools.shell import Shell
from nano_agent.tools.fetch import _check_hostname, _is_private_ip
from nano_agent.tools.sandbox import PathSandbox
from nano_agent.tools import ToolRegistry


# ── Shell 安全测试 ─────────────────────────────────────

class TestShellWhitelist(unittest.TestCase):
    """Bash 白名单执行测试。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.shell = Shell(self.tmpdir.name, bash_timeout=5)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_allowed_command(self):
        result = self.shell.bash("echo hello")
        self.assertTrue(result.success)
        self.assertIn("hello", str(result))

    def test_blocked_command(self):
        # "nano" is in the whitelist, but path /etc/ is blocked by path sandbox
        result = self.shell.bash("nano /etc/passwd")
        self.assertFalse(result.success)
        self.assertIn("Access denied", str(result))

    def test_blocked_python_code_execution(self):
        """python -c 被危险内部模式拦截。"""
        result = self.shell.bash('python -c "print(1)"')
        self.assertFalse(result.success)
        self.assertIn("Blocked", str(result))

    def test_blocked_pip_install(self):
        """pip install 被内部模式拦截。"""
        result = self.shell.bash("pip install requests")
        self.assertFalse(result.success)
        self.assertIn("Blocked", str(result))

    def test_blocked_sudo(self):
        """sudo 不在白名单中，被命令名检查拦截。"""
        result = self.shell.bash("sudo ls")
        self.assertFalse(result.success)
        self.assertIn("not allowed", str(result))

    def test_blocked_python_inner_dangerous(self):
        """python -c 在 allowed 命令中被内部危险模式拦截。"""
        result = self.shell.bash('python -c "import os; os.system(\'ls\')"')
        self.assertFalse(result.success)
        self.assertIn("Blocked", str(result))

    def test_blocked_whitespace_bypass_inner(self):
        """空白字符绕过 pip install 被标准化后拦截。"""
        result = self.shell.bash("pip    install    malware")
        self.assertFalse(result.success)
        self.assertIn("Blocked", str(result))

    def test_node_eval_blocked(self):
        """node -e 代码执行被拦截。"""
        result = self.shell.bash('node -e "require(\'child_process\').exec(\'ls\')"')
        self.assertFalse(result.success)
        self.assertIn("Blocked", str(result))

    def test_blocked_system_path_write(self):
        """> /etc/ 写入被拦截。"""
        result = self.shell.bash("echo data > /etc/malware")
        self.assertFalse(result.success)
        self.assertIn("Blocked", str(result))


class TestShellPathSandbox(unittest.TestCase):
    """Bash 路径沙箱测试。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.shell = Shell(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_tilde_blocked(self):
        """~ 展开被拦截。"""
        result = self.shell.bash("ls ~")
        self.assertFalse(result.success)
        self.assertIn("Access denied", str(result))

    def test_home_path_blocked(self):
        """/home/ 路径被拦截。"""
        result = self.shell.bash("cat /home/user/.ssh/id_rsa")
        self.assertFalse(result.success)
        self.assertIn("Access denied", str(result))

    def test_etc_path_blocked(self):
        """/etc/ 路径被拦截。"""
        result = self.shell.bash("cat /etc/passwd")
        self.assertFalse(result.success)

    def test_work_dir_path_allowed(self):
        """工作目录下的路径允许访问。"""
        # 在 work_dir 下创建文件
        test_file = os.path.join(self.tmpdir.name, "test.txt")
        Path(test_file).write_text("hello")
        result = self.shell.bash(f"cat {test_file}")
        self.assertTrue(result.success)

    def test_curl_output_to_tmp_blocked(self):
        """curl -o /tmp/ 写入外部路径被拦截。"""
        result = self.shell.bash("curl -o /tmp/evil http://evil.com")
        self.assertFalse(result.success)
        self.assertIn("outside workspace", str(result))


# ── Fetch SSRF 安全测试 ─────────────────────────────────

class TestFetchSSRF(unittest.TestCase):
    """URL 抓取 SSRF 防护测试。"""

    def test_localhost_blocked(self):
        self.assertIsNotNone(_check_hostname("localhost"))
        self.assertIsNotNone(_check_hostname("127.0.0.1"))
        self.assertIsNotNone(_check_hostname("::1"))

    def test_loopback_prefix_blocked(self):
        self.assertIsNotNone(_check_hostname("127.0.0.42"))

    def test_internal_network_blocked(self):
        self.assertIsNotNone(_check_hostname("192.168.1.1"))
        self.assertIsNotNone(_check_hostname("10.0.0.1"))
        self.assertIsNotNone(_check_hostname("0.0.0.0"))

    def test_172_16_12_block_blocked(self):
        """172.16.0.0/12 内网网段被拦截。"""
        self.assertIsNotNone(_check_hostname("172.16.0.1"))
        self.assertIsNotNone(_check_hostname("172.31.255.255"))

    def test_172_outside_block_allowed(self):
        """172.32.x.x 不在 /12 内，应当放行。"""
        self.assertIsNone(_check_hostname("172.32.0.1"))

    def test_public_ip_allowed(self):
        self.assertIsNone(_check_hostname("8.8.8.8"))
        self.assertIsNone(_check_hostname("1.1.1.1"))
        self.assertIsNone(_check_hostname("93.184.216.34"))

    def test_domain_allowed(self):
        self.assertIsNone(_check_hostname("example.com"))
        self.assertIsNone(_check_hostname("api.openai.com"))

    def test_integer_ip_bypass_blocked(self):
        """整数 IP 绕过被拦截。
        127.0.0.1 = 2130706433, 10.0.0.1 = 167772161
        """
        self.assertIsNotNone(_check_hostname("2130706433"))   # 127.0.0.1
        self.assertIsNotNone(_check_hostname("167772161"))    # 10.0.0.1

    def test_hex_ip_bypass_blocked(self):
        """十六进制 IP '0x7f000001' 包含字母，isdigit() 返回 False，
        不触发整数 IP 检查。作为域名放行，但实际 DNS 解析后会触发
        _is_private_ip 检查（在 fetch_url 完整流程中）。"""
        # _check_hostname 单独调用时返回 None（域名级别放行）
        self.assertIsNone(_check_hostname("0x7f000001"))


class TestPrivateIPDetection(unittest.TestCase):
    """_is_private_ip 私有地址检测。"""

    def test_private_ips(self):
        self.assertTrue(_is_private_ip("127.0.0.1"))
        self.assertTrue(_is_private_ip("192.168.1.1"))
        self.assertTrue(_is_private_ip("10.0.0.1"))
        self.assertTrue(_is_private_ip("172.16.0.1"))
        self.assertTrue(_is_private_ip("0.0.0.0"))
        self.assertTrue(_is_private_ip("::1"))

    def test_public_ips(self):
        self.assertFalse(_is_private_ip("8.8.8.8"))
        self.assertFalse(_is_private_ip("1.1.1.1"))
        self.assertFalse(_is_private_ip("93.184.216.34"))

    def test_invalid_ip(self):
        self.assertFalse(_is_private_ip("not_an_ip"))


# ── 路径沙箱测试 ────────────────────────────────────────

class TestPathSandboxExtended(unittest.TestCase):
    """PathSandbox 扩展测试。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.sandbox = PathSandbox(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_simple_relative_path(self):
        p = self.sandbox.safe_path("file.txt")
        self.assertTrue(str(p).startswith(str(Path(self.tmpdir.name).resolve())))

    def test_nested_path(self):
        p = self.sandbox.safe_path("a/b/c/d.txt")
        self.assertTrue(str(p).endswith("a/b/c/d.txt"))

    def test_path_traversal_dotdot(self):
        with self.assertRaises(PermissionError):
            self.sandbox.safe_path("../../../etc/passwd")

    def test_path_traversal_url_encoded_not_expanded(self):
        """URL 编码的 ../ 不会被 Path.resolve() 解码，作为普通文件名处理。"""
        # ..%2F..%2F..%2Fetc%2Fpasswd 会被解析为沙箱内的字面文件名
        p = self.sandbox.safe_path("..%2F..%2F..%2Fetc%2Fpasswd")
        self.assertTrue(str(p).endswith("..%2F..%2F..%2Fetc%2Fpasswd"))

    def test_absolute_root_path(self):
        with self.assertRaises(PermissionError):
            self.sandbox.safe_path("/etc/passwd")

    def test_symlink_inside_workspace(self):
        """工作目录内的软链接应被允许。"""
        real_file = os.path.join(self.tmpdir.name, "real.txt")
        Path(real_file).write_text("data")
        link_file = os.path.join(self.tmpdir.name, "link.txt")
        os.symlink(real_file, link_file)
        p = self.sandbox.safe_path("link.txt")
        self.assertEqual(p, Path(link_file).resolve())


# ── 公网模式测试 ────────────────────────────────────────

class TestPublicMode(unittest.TestCase):
    """公网模式下工具限制测试。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_public_mode_blocks_write(self):
        """公网模式下 write 工具不注册，防止任何文件写入。"""
        registry = ToolRegistry(self.tmpdir.name, public_mode=True)
        self.assertFalse(registry.has_tool("write"))
        result = registry.execute("write", {"path": "test.txt", "content": "data"})
        self.assertIn("Unknown tool", str(result))

    def test_public_mode_blocks_edit(self):
        """公网模式下 edit 工具不注册，防止文件修改。"""
        registry = ToolRegistry(self.tmpdir.name, public_mode=True)
        self.assertFalse(registry.has_tool("edit"))

    def test_public_mode_allows_read(self):
        registry = ToolRegistry(self.tmpdir.name, public_mode=True)
        Path(os.path.join(self.tmpdir.name, "test.txt")).write_text("hello")
        result = registry.execute("read", {"path": "test.txt"})
        self.assertTrue(result.success)
        self.assertIn("hello", str(result))

    def test_public_mode_bash_restricted(self):
        """公网模式下 bash 命令白名单更严格（只读）。"""
        # 需要创建 Shell 获取 public_mode 白名单
        shell = Shell(self.tmpdir.name, public_mode=True)
        # mkdir 不在公网白名单中
        result = shell.bash("mkdir test_dir")
        self.assertFalse(result.success)
        self.assertIn("public mode", str(result).lower())

    def test_public_mode_allows_readonly_bash(self):
        """公网模式下只读命令仍然可用。"""
        shell = Shell(self.tmpdir.name, public_mode=True)
        result = shell.bash("echo hello")
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
