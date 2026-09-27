"""抓取模块：URL → HTML → 正文文本。

安全要点（移植自上一个项目，改之前先读）：
  - URL 必须过 security.assert_safe_url（协议白名单 + 公网地址校验）。
  - **重定向必须逐跳校验**：若只校验首跳，302 → 169.254.169.254 就能拿到云主机元数据。
    所以这里关掉 requests 的自动跳转，手动一跳跃一验证。
  - 响应体有大小上限，避免超大页面把内存吃满。

已知现实（上一个项目已踩过）：主流招聘官网是 JS 渲染的 SPA，本地 requests 抓不到正文。
所以本模块只是 ingestion 的一条路径，不是主路径；主路径是「手动粘贴 + 文件导入」。
"""
from __future__ import annotations

import logging
from typing import Optional
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests

from .security import allowlist_mode, assert_safe_url, is_domain_allowed

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
]

REDIRECT_CODES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 4
MAX_BYTES = 3 * 1024 * 1024
MIN_TEXT_LEN = 200


def fetch_url(url: str, timeout: float = 15.0, *, enforce_allowlist: Optional[bool] = None):
    """
    抓取 URL 正文。

    返回 (text, error, warnings)。
      - text 为 None 表示失败，error 说明原因。
      - warnings 是非阻断提示（如「不在白名单」「robots.txt 禁止」），按 allowlist_mode 决定是否拦截。

    allowlist_mode:
      off     不限制域名
      warn    不拦截，只提示（默认）
      enforce 白名单外一律拒绝
    """
    warnings: List[str] = []
    mode = allowlist_mode()

    allowed, reason = is_domain_allowed(url)
    if not allowed:
        if mode == "enforce" or enforce_allowlist is True:
            return None, f"白名单拒绝：{reason}", warnings
        warnings.append(f"白名单提示：{reason}")

    # robots.txt 检查：即使域名放行，也不抓站点明确拒绝的路径。
    rb, rb_err = robots_allows(url)
    if rb is False:
        msg = f"目标站点的 robots.txt 禁止抓取该路径（{rb_err}）"
        if mode == "enforce":
            return None, msg, warnings
        warnings.append(msg + "。已仍尝试抓取，但请自行评估合规性。")

    try:
        current = assert_safe_url(url)
    except Exception as exc:  # noqa: BLE001
        return None, f"URL 安全校验未通过：{exc}", warnings

    for _hop in range(MAX_REDIRECTS + 1):
        resp = _get_once(current, timeout)
        if resp is None:
            return None, "请求失败（网络或超时）", warnings

        if resp.status_code in REDIRECT_CODES:
            location = resp.headers.get("location")
            resp.close()
            if not location:
                return None, "重定向缺少 Location 头", warnings
            next_url = urljoin(current, location)
            if enforce_allowlist:
                ok2, why2 = is_domain_allowed(next_url)
                if not ok2:
                    return None, f"重定向目标被白名单拒绝：{why2}", warnings
            try:
                current = assert_safe_url(next_url)
            except Exception as exc:  # noqa: BLE001
                return None, f"重定向目标未通过安全校验：{exc}", warnings
            continue

        if resp.status_code >= 400:
            code = resp.status_code
            resp.close()
            return None, f"HTTP {code}", warnings

        html = _read_limited(resp)
        resp.close()
        if not html:
            return None, "响应体为空或超过体积上限", warnings

        text = clean_html(html)
        if text and len(text) >= MIN_TEXT_LEN:
            return text, "", warnings
        return None, (f"正文抽取失败或过短（{len(text or '')} 字符）。"
                      "该页可能是 JS 渲染的 SPA——请改用「手动粘贴」或「文件导入」。"), warnings

    return None, f"重定向次数超过 {MAX_REDIRECTS}", warnings


def _get_once(url: str, timeout: float) -> Optional[requests.Response]:
    last_err: Optional[Exception] = None
    for ua in USER_AGENTS:
        try:
            return requests.get(
                url,
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    logger.error("全部 UA 均失败：%s（%s）", url, last_err)
    return None


def _read_limited(resp: requests.Response, max_bytes: int = MAX_BYTES) -> Optional[str]:
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                logger.warning("响应体超过 %d 字节，已截断", max_bytes)
                break
            chunks.append(chunk)
    except Exception as exc:  # noqa: BLE001
        logger.error("读取响应体失败：%s", exc)
        return None
    raw = b"".join(chunks)
    if not raw:
        return None
    try:
        return raw.decode(resp.encoding or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def clean_html(html: str) -> Optional[str]:
    """HTML → 正文文本。优先 trafilatura，失败回退 BeautifulSoup。"""
    text = None
    try:
        import trafilatura
        text = trafilatura.extract(html, include_tables=True, favor_recall=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("trafilatura 抽取失败，回退 BeautifulSoup：%s", exc)

    if not text:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
                tag.decompose()
            text = soup.get_text("\n")
        except Exception as exc:  # noqa: BLE001
            logger.error("BeautifulSoup 抽取失败：%s", exc)
            return None

    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def read_uploaded_file(filename: str, data: bytes):
    """文件导入：支持 .txt/.md/.html，以及 .docx/.pdf（尽力而为）。返回 (text, error)。"""
    lower = (filename or "").lower()
    try:
        if lower.endswith((".txt", ".md", ".markdown")):
            return data.decode("utf-8", errors="replace"), ""
        if lower.endswith((".html", ".htm")):
            t = clean_html(data.decode("utf-8", errors="replace"))
            return (t, "") if t else (None, "HTML 正文抽取失败")
        if lower.endswith(".docx"):
            return _read_docx(data)
        if lower.endswith(".pdf"):
            return _read_pdf(data)
    except Exception as exc:  # noqa: BLE001
        return None, f"文件解析异常：{exc}"
    return None, f"不支持的文件类型：{filename}（支持 txt/md/html/docx/pdf）"


def _read_docx(data: bytes):
    import io
    import zipfile
    import re as _re
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = _re.sub(r"</w:p>", "\n", xml)
    text = _re.sub(r"<[^>]+>", "", xml)
    text = (text.replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'"))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return ("\n".join(lines), "") if lines else (None, "docx 未解析出文本")


def _read_pdf(data: bytes):
    try:
        from pypdf import PdfReader  # type: ignore
        import io
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
        return (text, "") if text.strip() else (None, "pdf 未解析出文本")
    except ImportError:
        return None, "未安装 pypdf，暂不支持 PDF 导入（可改用 txt/md/docx）"
    except Exception as exc:  # noqa: BLE001
        return None, f"pdf 解析失败：{exc}"


_ROBOTS_CACHE: Dict[str, Optional[bool]] = {}


def robots_allows(url: str) -> tuple[Optional[bool], str]:
    """
    检查目标路径是否被 robots.txt 允许。
    返回 (是否允许, 说明)。None 表示无法判定（无 robots.txt 或解析失败）——按宽松处理并提示。
    这是一个礼貌性检查，不是法律意见；真要合规请人工核对目标站点的服务条款。
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return None, "无主机名"
    if host in _ROBOTS_CACHE:
        cached = _ROBOTS_CACHE[host]
        return (None, "已缓存：无法判定") if cached is None else (cached, "已缓存")

    root = f"{parsed.scheme}://{host}/robots.txt"
    try:
        resp = requests.get(root, headers={"User-Agent": USER_AGENTS[0]}, timeout=8)
        if resp.status_code >= 400:
            _ROBOTS_CACHE[host] = None
            return None, f"robots.txt 返回 {resp.status_code}，按无限制处理"
        body = resp.text
    except Exception as exc:  # noqa: BLE001
        _ROBOTS_CACHE[host] = None
        return None, f"读取 robots.txt 失败：{exc}"

    path = parsed.path or "/"
    # 只做最粗的判定：User-agent: * 段里是否出现 Disallow: <该路径前缀>
    star = False
    dis = []
    for line in body.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("user-agent:"):
            star = low.split(":", 1)[1].strip() == "*"
        elif star and low.startswith("disallow:"):
            p = line.split(":", 1)[1].strip()
            if p:
                dis.append(p)
    for p in dis:
        if path.startswith(p):
            _ROBOTS_CACHE[host] = False
            return False, f"命中 Disallow: {p}"
    _ROBOTS_CACHE[host] = True
    return True, "未被禁止"
