# -*- coding: utf-8 -*-
"""Windows 平台特定能力（唯一的 OS 耦合层）。

目前仅读取注册表中的系统代理（IE/WinINET 共享设置）。
保持为独立模块，便于将来扩展其他平台或在非 Windows 环境下降级。
"""


def read_system_proxy():
    """读取 Windows 系统代理（即浏览器使用的代理设置，IE/WinINET 共享）。

    注册表 HKCU\\...\\Internet Settings：
      ProxyEnable=1 且 ProxyServer 形如 "127.0.0.1:7890" 或
      "http=...;https=...;ftp=..."（按协议分别指定时取 https/http 条目）。
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        try:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enable:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        finally:
            winreg.CloseKey(key)
    except OSError:
        return None
    server = (server or "").strip()
    if not server:
        return None
    if "=" in server:  # 按协议分别指定：http=host:port;https=host:port;...
        picked = None
        for part in server.split(";"):
            proto, _, addr = part.partition("=")
            if proto.strip().lower() == "https":
                picked = addr.strip()
                break
            if proto.strip().lower() == "http" and picked is None:
                picked = addr.strip()
        server = picked or ""
    if not server:
        return None
    if "://" not in server:
        server = "http://" + server
    return server

# 向后兼容别名（core.get_proxy 内部按此名调用）
_registry_proxy = read_system_proxy
