"""Shell 工具：bash, calculate。"""

import ast
import operator as _op
import os
import shlex
import subprocess

# ── 危险命令白名单（只允许这些前缀的命令通过）────────────
_SAFE_COMMAND_PREFIXES = [
    "ls", "cat", "head", "tail", "wc", "find", "grep", "git",
    "pwd", "echo", "date", "whoami", "uname", "env", "printenv",
    "python", "python3", "node", "npm", "npx", "tsc", "cargo", "go", "sleep",
    "mkdir", "touch", "cp", "mv", "rm", "chmod", "chown",
    "curl", "wget", "diff", "sort", "uniq", "cut", "sed", "awk", "tr",
    "which", "command", "type", "file", "stat", "du", "df",
    "pip", "pip3", "poetry", "uv", "cargo",
    "nano", "code",
]
_SAFE_COMMAND_PREFIXES_TUPLE = tuple(_SAFE_COMMAND_PREFIXES)


class Shell:
    def __init__(self, work_dir: str, bash_timeout: int = 120):
        self.work_dir = work_dir
        self.bash_timeout = bash_timeout

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

        try:
            r = subprocess.run(
                parts,
                shell=False,
                cwd=self.work_dir,
                capture_output=True,
                text=True,
                timeout=self.bash_timeout,
            )
            out = (r.stdout + r.stderr).strip()
            return out[:50000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: Timeout ({self.bash_timeout}s)"
        except FileNotFoundError:
            return f"Error: Command not found: {parts[0]}"
        except OSError as e:
            return f"Error: {e}"

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
            return f"{expression} = {result}"
        except ZeroDivisionError:
            return "Error: Division by zero"
        except Exception as e:
            return f"Error: {e}"
