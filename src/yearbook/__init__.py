"""麦麦年鉴 McYearbook —— 可溯源的麦当劳消费年度回顾。

本包只依赖 Python 标准库，不引入任何第三方依赖，确保 clone 之后即可运行。

模块职责：
    models     数据模型与证据（Evidence）结构定义
    normalize  官方 MCP 原始响应 -> 领域模型
    analyze    确定性统计，每个结论附带可回溯的证据链
    render     单文件 HTML 报告渲染
    cli        命令行入口
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
