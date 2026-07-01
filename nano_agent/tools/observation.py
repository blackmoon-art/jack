"""Observation — 工具执行的统一返回类型。

所有工具必须返回 Observation 对象，而非裸字符串。
execute() 内部会自动包装裸字符串，但新工具应直接构造 Observation。
"""

from dataclasses import dataclass, field


@dataclass
class Observation:
    """结构化工具返回 — 统一 Observation 层。

    所有工具执行后返回 Observation，包含:
      - tool_name:    调用的工具名
      - success:      是否成功
      - result:       结果文本 (或错误信息)
      - args:         调用参数
      - metadata:     额外信息 (如执行时间、截断标志等)

    __str__ 返回 result，兼容旧代码的字符串处理。
    """
    tool_name: str
    result: str
    success: bool = True
    args: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return self.result

    def __contains__(self, substr: str) -> bool:
        return substr in self.result

    def __len__(self) -> int:
        return len(self.result)

    def __getitem__(self, key):
        return self.result[key]

    def __eq__(self, other) -> bool:
        if isinstance(other, Observation):
            return self.tool_name == other.tool_name and self.result == other.result
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.tool_name, self.result))

    def __bool__(self) -> bool:
        return self.success

    @staticmethod
    def ok(tool_name: str, result: str, **metadata) -> "Observation":
        """快捷构造成功 Observation。"""
        return Observation(tool_name=tool_name, result=result, success=True, metadata=metadata)

    @staticmethod
    def error(tool_name: str, message: str, **metadata) -> "Observation":
        """快捷构造失败 Observation。"""
        return Observation(tool_name=tool_name, result=f"Error: {message}", success=False, metadata=metadata)
