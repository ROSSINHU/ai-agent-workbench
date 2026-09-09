# -*- coding: utf-8 -*-
"""AI Agent 工作台启动入口（兼容原有启动方式）。

实际实现已按层拆分到 workbench 包：
- workbench.platform_windows：Windows 平台特定能力（注册表代理）
- workbench.core：headless 核心逻辑（配置 / 进程 / 代理 / Node / 日志 / 状态）
- workbench.app：Tkinter 图形界面

保留本文件是为了让现有启动器（AI agent管理工作台.bat）与文档中的
`python service_manager.py` 启动方式保持不变。
"""
from workbench.app import main


if __name__ == "__main__":
    main()
