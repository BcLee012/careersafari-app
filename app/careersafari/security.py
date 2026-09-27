"""
URL 安全校验（SSRF 防护）与写操作鉴权。

移植自上一个项目 prototype/app/security.py 的同名模块，保留其全部设计取舍：
  - 写操作一律要求 X-Admin-Token；ADMIN_TOKEN 未配置时**直接关闭写操作（fail closed）**，
    而不是放行。原型阶段"为了本地方便默认放行"的写法，一旦部署到公网就是后门。
  - 限流是进程内滑动窗口：单进程部署有效，多 worker 下是「每 worker 一份配额」。
  - SSRF 防护同时校验协议、主机名、DNS 解析出的**全部** IP，并**逐跳校验重定向**
    （否则 302 到 169.254.169.254 就能绕过首跳检查，拿到云主机元数据）。
  - IPv4-mapped IPv6（::ffff:127.0.0.1）必须先拆开再判断，否则能绕过。
"""
from __future__ import annotations

import ipaddress
import logging
import os
import secrets
import socket
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ADMIN_TOKEN_ENV = "ADMIN_TOKEN"
ALLOW_NON_PUBLIC_ENV = "FETCH_ALLOW_NON_PUBLIC"
ALLOWED_SCHEMES = {"http", "https"}

# 受控采集白名单（域名后缀 / 子域名标签）。
# 只列「允许抓」的来源：高校、政府、企业官网校招页。
# 注意：智联 / BOSS 直聘 / 猎聘 / 牛客 不在此列 —— 反爬严格，且批量抓取有合规风险。
DEFAULT_ALLOWED_SUFFIXES = [
    ".edu.cn",
    ".gov.cn",
    ".jobs",
    "careers.",
    "campus.",
    "hr.",
    "join.",
    "recruit.",
    "jobs.",
]

# 白名单模式：
#   off     —— 不限制域名，任何 URL 都允许抓（需自行承担合规与反爬后果）
#   warn    —— 不拦截，但在抓取前返回提示，让调用方知情（默认）
#   enforce —— 白名单外一律拒绝
ALLOWLIST_MODE_ENV = "FETCH_ALLOWLIST_MODE"
DEFAULT_ALLOWLIST_MODE = "warn"


def allowlist_mode() -> str:
    v = (os.environ.get(ALLOWLIST_MODE_ENV) or DEFAULT_ALLOWLIST_MODE).strip().lower()
    return v if v in ("off", "warn", "enforce") else DEFAULT_ALLOWLIST_MODE


def _load_env_file() -> None:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            # 空值不覆盖已有配置：否则 .env 里留空的 ADMIN_TOKEN= 会屏蔽后面自动生成的值
            if v:
                os.environ.setdefault(k, v)


_load_env_file()


# ---------------------------------------------------------------- 管理令牌


def configured_admin_token() -> Optional[str]:
    return (os.environ.get(ADMIN_TOKEN_ENV) or "").strip() or None


def require_admin(x_admin_token: Optional[str]) -> None:
    """写操作鉴权。未配置 ADMIN_TOKEN → 503（fail closed）。"""
    expected = configured_admin_token()
    if not expected:
        raise PermissionError(
            "写操作未启用：服务端未配置 ADMIN_TOKEN。请在 app/.env 设置后重启。"
            "这是防止公网部署时写接口裸奔的开关。"
        )
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise PermissionError("无效或缺失的管理令牌。")


# ---------------------------------------------------------------- 限流


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_seconds: float, *, max_keys: int = 10_000):
        self.max_events = max_events
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > self.max_keys:
                self._hits.clear()
            q = self._hits[key]
            while q and now - q[0] > self.window_seconds:
                q.popleft()
            if len(q) >= self.max_events:
                retry_after = self.window_seconds - (now - q[0])
                raise PermissionError(
                    f"请求过于频繁，请 {max(1, int(retry_after))} 秒后重试。"
                )
            q.append(now)


# 抓取接口最耗资源（要发外部请求），配额收紧
parse_limiter = SlidingWindowLimiter(max_events=10, window_seconds=60)
read_limiter = SlidingWindowLimiter(max_events=120, window_seconds=60)


# ---------------------------------------------------------------- SSRF 防护


_warned_non_public = False


def _allow_non_public() -> bool:
    global _warned_non_public
    v = (os.environ.get(ALLOW_NON_PUBLIC_ENV) or "").strip().lower()
    on = v in {"1", "true", "yes", "on"}
    if on and not _warned_non_public:
        _warned_non_public = True
        logger.warning(
            "\n\n"
            "  !!! FETCH_ALLOW_NON_PUBLIC=1 —— SSRF 公网地址校验已关闭 !!!\n"
            "  仅用于本地开发（如透明代理沙箱把全部域名解析到 198.18.x.x 导致误杀）。\n"
            "  公网部署必须关闭此项。\n"
        )
    return on


def _check_addr(addr, raw: str) -> None:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped  # ::ffff:127.0.0.1 必须先拆开
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved \
            or addr.is_multicast or addr.is_unspecified:
        raise ValueError(f"拒绝访问非公网地址：{raw}")
    # 云元数据地址 169.254.169.254 已被 is_link_local 覆盖，这里显式再拦一次以防平台差异
    if str(addr) == "169.254.169.254":
        raise ValueError("拒绝访问云元数据地址")


def assert_safe_url(url: str) -> str:
    """校验 URL 安全性，返回规范化后的 URL。不合格抛 ValueError。"""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError(f"仅允许 http/https 协议，收到：{parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ValueError("URL 缺少主机名")

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"域名解析失败：{host}（{exc}）") from exc

    addrs = {info[4][0] for info in infos}
    if not addrs:
        raise ValueError(f"域名未解析出地址：{host}")

    for raw in addrs:
        try:
            addr = ipaddress.ip_address(raw.split("%")[0])
        except ValueError:
            continue
        if _allow_non_public():
            continue
        _check_addr(addr, raw)

    return url


def is_domain_allowed(url: str) -> tuple[bool, str]:
    """
    受控采集白名单。

    语义区分：
      - 以 "." 开头的条目（.edu.cn / .gov.cn）→ 按**域名后缀**匹配；
      - 其余条目（careers. / hr. / campus. …）→ 按**子域名标签**匹配，
        即 host 等于该标签，或以 ".该标签" 开头（hr.jd.com → "hr." 命中）。
    """
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False, "无主机名"
    for rule in _allowed_suffixes():
        if rule.startswith("."):
            if host.endswith(rule):
                return True, f"命中域名后缀白名单 {rule}"
        else:
            label = rule.rstrip(".")
            if host == label or host.startswith(label + "."):
                return True, f"命中子域名白名单 {label}."
    return False, ("不在受控采集白名单内。白名单只覆盖高校/政府/企业官网校招页，"
                   "不含智联/BOSS/猎聘等招聘平台——它们反爬严格（多为 JS 渲染 SPA，"
                   "本地抓不到正文），且批量抓取存在合规风险。")


def _allowed_suffixes() -> list[str]:
    extra = (os.environ.get("FETCH_ALLOWED_SUFFIXES") or "").strip()
    if extra:
        return [x.strip() for x in extra.split(",") if x.strip()]
    return DEFAULT_ALLOWED_SUFFIXES
