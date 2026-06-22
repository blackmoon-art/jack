"""路径沙箱：限制文件读写在工作目录内。"""

from pathlib import Path


class PathSandbox:
    """文件操作沙箱：限制读写在工作目录内。"""

    def __init__(self, root: str):
        self._root = Path(root).resolve()

    def safe_path(self, user_path: str) -> Path:
        """将用户给的路径解析为沙箱内的绝对路径，越界则拒绝。"""
        p = (self._root / user_path).resolve()
        try:
            p.relative_to(self._root)
        except ValueError:
            raise PermissionError(f"Access denied: '{user_path}' is outside workspace")
        return p
