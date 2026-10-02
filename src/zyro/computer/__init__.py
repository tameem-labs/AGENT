"""Computer, browser, and screen interaction capabilities for ZYRO."""

from zyro.computer.browser import BrowserNavigateHandler, validate_browser_url
from zyro.computer.control import (
    WindowsFocusWindowHandler,
    WindowsListWindowsHandler,
    WindowsSandboxedExecHandler,
)
from zyro.computer.screen import ScreenCaptureHandler, ScreenFrame

__all__ = [
    "BrowserNavigateHandler",
    "ScreenCaptureHandler",
    "ScreenFrame",
    "WindowsFocusWindowHandler",
    "WindowsListWindowsHandler",
    "WindowsSandboxedExecHandler",
    "validate_browser_url",
]
