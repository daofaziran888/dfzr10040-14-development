"""业务路由汇总：固定的影响分析路由必须在通用动态资源路由之前注册。"""
from __future__ import annotations

from app.routers import impact, resource

ROUTERS = [impact, resource]
