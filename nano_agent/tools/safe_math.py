"""安全数学表达式求值器 — 基于 ast.parse 白名单。

共享实现：被 shell.py (calculate) 和 chart/advanced.py (function/surface/contour) 使用。
只允许数字、变量（需在 namespace 中）、四则运算、幂运算、函数调用。
不允许属性访问、下标、比较、推导式等。
"""

import ast
import operator as _op


def safe_eval(expr_str: str, namespace: dict | None = None):
    """安全求值数学表达式。

    Args:
        expr_str:   数学表达式字符串 (如 "sin(x) + 3**2")
        namespace:  变量和函数的命名空间。Name 节点必须在此 dict 中。
                    函数调用只允许 namespace 中的可调用对象。

    Returns:
        求值结果，或抛出 ValueError / NameError / ZeroDivisionError

    Example:
        >>> safe_eval("3 + 4 * 2")           # 11
        >>> safe_eval("sin(x)", {"x": 1.57, "sin": __import__('math').sin})
    """
    ns = namespace or {}

    _BINOPS = {
        ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul,
        ast.Div: _op.truediv, ast.FloorDiv: _op.floordiv,
        ast.Mod: _op.mod, ast.Pow: _op.pow,
    }
    _UNOPS = {ast.USub: _op.neg, ast.UAdd: _op.pos}

    def _eval(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in ns:
                return ns[node.id]
            raise NameError(f"name '{node.id}' is not defined")
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            op_cls = type(node.op)
            if op_cls not in _BINOPS:
                raise ValueError(f"Unsupported operator: {op_cls.__name__}")
            if op_cls is ast.Div and right == 0:
                raise ZeroDivisionError("division by zero")
            return _BINOPS[op_cls](left, right)
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            op_cls = type(node.op)
            if op_cls not in _UNOPS:
                raise ValueError(f"Unsupported unary: {op_cls.__name__}")
            return _UNOPS[op_cls](operand)
        if isinstance(node, ast.Call):
            func = _eval(node.func)
            if not callable(func):
                raise ValueError(f"'{type(func).__name__}' is not callable")
            args = [_eval(a) for a in node.args]
            if node.keywords:
                raise ValueError("Keyword arguments not allowed")
            return func(*args)
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        raise ValueError(f"Unsupported expression element: {type(node).__name__}")

    tree = ast.parse(expr_str.strip(), mode="eval")
    return _eval(tree)
