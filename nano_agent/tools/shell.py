"""Shell 工具：bash, calculate。"""

import ast
import operator as _op
import os
import shlex
import subprocess
from typing import Any, Optional

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
    "nano", "code", "bash",
]
_SAFE_COMMAND_PREFIXES_TUPLE = tuple(_SAFE_COMMAND_PREFIXES)


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

    def __init__(self, work_dir: str, bash_timeout: int = 120):
        self.work_dir = work_dir
        self.bash_timeout = bash_timeout
        # 自动检测 venv，把 .venv/bin 加到 PATH
        self._env = os.environ.copy()
        venv_bin = os.path.join(work_dir, '.venv', 'bin')
        if os.path.isdir(venv_bin):
            self._env['PATH'] = venv_bin + os.pathsep + self._env.get('PATH', '')
            self._env['VIRTUAL_ENV'] = os.path.join(work_dir, '.venv')

    # 危险模式黑名单 — bash -c 内部不允许的命令
    _DANGEROUS_PATTERNS = (
        'rm -rf /', 'mkfs', 'dd if=', ':(){:|:&}', 'format c:',
        'shutdown', 'reboot', 'init 0', 'init 6',
        'curl', 'wget',  # 防止下载执行
        'chmod 777', 'chown',  # 权限提升
        'sudo', 'su ',  # 提权
        '>/etc/', '>/var/', '>/sys/',  # 写系统目录
        'nc -', 'ncat', 'socat',  # 反弹 shell
        'python -c', 'python3 -c', 'node -e', 'perl -e', 'ruby -e',  # 脚本注入
        'import os', 'os.system', 'subprocess',  # Python 注入
        '| bash', '| sh', '| zsh',  # 管道执行
    )

    def bash(self, command: str) -> str:
        """安全执行 bash 命令：shell=False + shlex + 白名单前缀。"""
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return f"Error: Invalid shell syntax — {e}"
        if not parts:
            return "Error: Empty command"

        cmd_name = os.path.basename(parts[0])
        if not cmd_name.startswith(_SAFE_COMMAND_PREFIXES_TUPLE):
            return (
                f"Error: Command '{parts[0]}' is not in the allowed list. "
                f"Allowed prefixes: {', '.join(_SAFE_COMMAND_PREFIXES[:15])}..."
            )

        # bash -c 安全检查：对内部命令做递归白名单 + 危险模式黑名单
        if cmd_name == 'bash' and '-c' in parts:
            inner_cmd = ' '.join(parts[parts.index('-c') + 1:])
            inner_lower = inner_cmd.lower()
            # 黑名单检查
            for pattern in self._DANGEROUS_PATTERNS:
                if pattern.lower() in inner_lower:
                    return f"Error: Dangerous pattern blocked in bash -c command: {pattern}"
            # 递归白名单：内部命令的第一个词也必须在白名单中
            try:
                inner_parts = shlex.split(inner_cmd)
            except ValueError:
                return "Error: Invalid inner command in bash -c"
            if inner_parts:
                inner_cmd_name = os.path.basename(inner_parts[0])
                if not inner_cmd_name.startswith(_SAFE_COMMAND_PREFIXES_TUPLE):
                    return (
                        f"Error: Inner command '{inner_cmd_name}' in bash -c "
                        f"is not in the allowed list."
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
            text = out[:50000] if out else "(no output)"
            return Observation(
                tool_name="bash",
                result=text,
                success=(r.returncode == 0),
                args={"command": command},
                metadata={
                    "exit_code": r.returncode,
                    "truncated": len(out) > 50000,
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

    def calculate(self, expression: str) -> str:
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
