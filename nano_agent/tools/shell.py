"""Shell 工具：bash, calculate。"""

import ast
import operator as _op
import os
import shlex
import subprocess
from typing import Any

from .observation import Observation

# ── 危险命令白名单（只允许这些前缀的命令通过）────────────
_SAFE_COMMAND_PREFIXES = [
    "ls", "cat", "head", "tail", "wc", "find", "grep", "git",
    "pwd", "echo", "date", "whoami", "uname", "env", "printenv",
    "python", "python3", "node", "npm", "npx", "tsc", "cargo", "go", "sleep",
    "mkdir", "touch", "cp", "mv", "rm", "chmod", "chown",
    "curl", "wget", "diff", "sort", "uniq", "cut", "sed", "awk", "tr",
    "which", "command", "type", "file", "stat", "du", "df",
    "pip", "pip3", "poetry", "uv", "cargo",
    "nano",
]
_SAFE_COMMAND_PREFIXES_TUPLE = tuple(_SAFE_COMMAND_PREFIXES)

# ── 公网模式白名单（严格只读，禁止代码执行/文件修改/网络下载/包安装）───
_PUBLIC_SAFE_COMMANDS = [
    "ls", "cat", "head", "tail", "wc", "find", "grep",
    "pwd", "echo", "date", "whoami", "uname", "env", "printenv",
    "which", "command", "type", "file", "stat", "du", "df",
    "diff", "sort", "uniq", "cut", "tr",
]
_PUBLIC_SAFE_COMMANDS_TUPLE = tuple(_PUBLIC_SAFE_COMMANDS)


class Shell:
    # 工具注册声明
    TOOLS = [
        ("bash", "Run a shell command.", "bash",
         {"command": {"type": "string", "description": "The command to execute"}},
         ["command"]),
        ("calculate", "Evaluate a mathematical expression safely.", "calculate",
         {"expression": {"type": "string", "description": "Math expression (e.g. '2+3*4')"}},
         ["expression"]),
    ]

    def __init__(self, work_dir: str, bash_timeout: int = 120, public_mode: bool = False,
                 output_limit: int = 50000):
        self.work_dir = work_dir
        self.bash_timeout = bash_timeout
        self.public_mode = public_mode
        self.output_limit = output_limit
        self._safe_prefixes = _PUBLIC_SAFE_COMMANDS_TUPLE if public_mode else _SAFE_COMMAND_PREFIXES_TUPLE
        # 自动检测 venv，把 .venv/bin 加到 PATH
        self._env = os.environ.copy()
        venv_bin = os.path.join(work_dir, '.venv', 'bin')
        if os.path.isdir(venv_bin):
            self._env['PATH'] = venv_bin + os.pathsep + self._env.get('PATH', '')
            self._env['VIRTUAL_ENV'] = os.path.join(work_dir, '.venv')

    # 路径沙箱：用 realpath 解析而非字符串匹配
    _BLOCKED_PATH_TOKENS = ('~', '../', '/Users/', '/home/', '/etc/', '/var/',
                            '/sys/', '/proc/', '/root/', '/tmp/', '/private/')

    def _check_path_sandbox(self, command: str) -> str | None:
        """检查命令是否试图访问工作目录外的路径。返回错误信息或 None。

        改进：除了字符串黑名单，还用 os.path.realpath 对所有路径类参数
        做实际解析，确保 resolved path 在 work_dir 内。
        """
        # 1. 快速字符串检查：阻止 ~ 和明显的敏感路径
        for token in self._BLOCKED_PATH_TOKENS:
            if token in command:
                if token == '../':
                    pass  # 由下面的 realpath 检查处理
                # 允许 work_dir 下的路径（如 /Users/xxx/agent_workspace/...）
                elif self.work_dir in command:
                    continue
                else:
                    return f"Access denied: path '{token}' is outside workspace"
        # 2. 阻止 ~ 展开
        if '~' in command:
            return "Access denied: '~' is not allowed (workspace-only access)"
        # 3. 阻止 symlink 逃逸：对 shlex 解析后的每个路径类参数做 realpath 检查
        try:
            test_parts = shlex.split(command)
        except ValueError:
            return None  # 语法错误由 bash() 处理
        work_dir_real = os.path.realpath(self.work_dir)
        for part in test_parts:
            # 跳过选项（-x, --xxx）和不含路径的命令名/参数
            if part.startswith('-') or '/' not in part:
                continue
            # 对含路径的参数做 realpath 解析
            resolved = os.path.realpath(os.path.join(work_dir_real, part))
            if not resolved.startswith(work_dir_real + os.sep) and resolved != work_dir_real:
                return f"Access denied: path escapes workspace ('{part}' → {resolved})"
        return None

    def bash(self, command: str) -> Observation:
        """安全执行 bash 命令：shell=False + shlex + 白名单前缀。"""
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return Observation.error("bash", f"Invalid shell syntax — {e}", args={"command": command})
        if not parts:
            return Observation.error("bash", "Empty command", args={"command": command})

        cmd_name = os.path.basename(parts[0])
        if not cmd_name.startswith(self._safe_prefixes):
            return Observation.error(
                "bash",
                f"Command '{parts[0]}' is not allowed{f' (public mode)' if self.public_mode else ''}. "
                f"Allowed: {', '.join(self._safe_prefixes)}",
                args={"command": command},
            )

        # 危险内部命令黑名单：即使白名单命令也阻止危险的二级执行
        _DANGEROUS_INNER = (
            # 代码执行 — 可绕过沙箱
            "python -c", "python3 -c", "python -C", "python3 -C",
            "node -e", "perl -e", "ruby -e",
            # 提权
            "sudo", "su ", "doas ",
            # 管道执行 / 远程脚本执行
            "curl |", "curl|", "wget |", "wget|",
            "| bash", "|sh", "| zsh", "| python", "|python",
            "> /dev/tcp", "> /dev/udp",
            # 反弹 shell 模式
            "/dev/tcp/", "/dev/udp/",
            # 包安装（可能引入恶意包）
            "pip install", "pip3 install", "pip3.12 install",
            "npm install", "npm i ", "npx ",
            "cargo install",
            # crontab 篡改
            "crontab",
            # 写入系统路径
            "> /etc/", "> /var/", "> /sys/", "> /proc/",
            ">> /etc/", ">> /var/", ">> /sys/", ">> /proc/",
        )
        # 标准化空白字符（防空格绕过：sudo  apt 不匹配 "sudo"）
        import re as _re
        cmd_normalized = _re.sub(r'\s+', ' ', command.lower())
        for danger in _DANGEROUS_INNER:
            # 同样标准化 danger pattern（含空格的模式如 "pip install"）
            danger_norm = _re.sub(r'\s+', ' ', danger)
            if danger_norm in cmd_normalized:
                return Observation.error(
                    "bash",
                    f"Blocked: dangerous pattern '{danger.strip()}' detected in command",
                    args={"command": command},
                )

        # 路径沙箱：禁止访问工作目录外部
        path_error = self._check_path_sandbox(command)
        if path_error:
            return Observation.error("bash", path_error, args={"command": command})

        # 禁止写入工作目录外的文件（curl -o /tmp/x 等）
        _write_outside = (
            "-o /tmp/", "-o /var/", "-o /etc/", "-o /Users/", "-o /home/",
            "--output /tmp/", "--output /var/", "--output /etc/",
            "--output /Users/", "--output /home/",
        )
        for pattern in _write_outside:
            if pattern in command:
                return Observation.error(
                    "bash",
                    f"Blocked: writing to path outside workspace ('{pattern})",
                    args={"command": command},
                )

        try:
            r = subprocess.run(
                parts,
                shell=False,
                cwd=self.work_dir,
                capture_output=True,
                text=True,
                timeout=self.bash_timeout,
                env=self._env,
            )
            out = (r.stdout + r.stderr).strip()
            text = out[:self.output_limit] if out else "(no output)"
            return Observation(
                tool_name="bash",
                result=text,
                success=(r.returncode == 0),
                args={"command": command},
                metadata={
                    "exit_code": r.returncode,
                    "truncated": len(out) > self.output_limit,
                    "output_length": len(out),
                },
            )
        except subprocess.TimeoutExpired:
            return Observation(
                tool_name="bash", result=f"Error: Timeout ({self.bash_timeout}s)",
                success=False, args={"command": command}, metadata={},
            )
        except FileNotFoundError:
            return Observation(
                tool_name="bash", result=f"Error: Command not found: {parts[0]}",
                success=False, args={"command": command}, metadata={},
            )
        except OSError as e:
            return Observation(
                tool_name="bash", result=f"Error: {e}",
                success=False, args={"command": command}, metadata={},
            )

    def calculate(self, expression: str) -> Observation:
        """安全计算数学表达式（使用 ast 解析，无 eval）。"""

        _ALLOWED_OPS = {
            ast.Add: _op.add,
            ast.Sub: _op.sub,
            ast.Mult: _op.mul,
            ast.Div: _op.truediv,
            ast.FloorDiv: _op.floordiv,
            ast.Mod: _op.mod,
            ast.Pow: _op.pow,
            ast.USub: _op.neg,
            ast.UAdd: _op.pos,
        }

        def _eval_ast(node):
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.BinOp):
                left = _eval_ast(node.left)
                right = _eval_ast(node.right)
                op_cls = type(node.op)
                if op_cls not in _ALLOWED_OPS:
                    raise ValueError(f"Unsupported operator: {op_cls.__name__}")
                if op_cls is ast.Div and right == 0:
                    raise ZeroDivisionError("division by zero")
                return _ALLOWED_OPS[op_cls](left, right)
            if isinstance(node, ast.UnaryOp):
                operand = _eval_ast(node.operand)
                op_cls = type(node.op)
                if op_cls not in _ALLOWED_OPS:
                    raise ValueError(f"Unsupported unary: {op_cls.__name__}")
                return _ALLOWED_OPS[op_cls](operand)
            if isinstance(node, ast.Expression):
                return _eval_ast(node.body)
            raise ValueError(f"Unsupported expression element: {type(node).__name__}")

        try:
            tree = ast.parse(expression.strip(), mode="eval")
            result = _eval_ast(tree)
            return Observation(
                tool_name="calculate",
                result=f"{expression} = {result}",
                success=True,
                args={"expression": expression},
            )
        except ZeroDivisionError:
            return Observation(
                tool_name="calculate",
                result="Error: Division by zero",
                success=False,
                args={"expression": expression},
            )
        except Exception as e:
            return Observation(
                tool_name="calculate",
                result=f"Error: {e}",
                success=False,
                args={"expression": expression},
            )
