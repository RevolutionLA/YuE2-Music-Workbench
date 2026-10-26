"""局域网直连网关的可选令牌校验。

设计边界：
  * 回环客户端（本机 UI / dsh 反代）永远免令牌，避免把自己锁在门外。
  * 只有设置了 YUE2_ACCESS_TOKEN 才启用；未设置时行为与旧版一致。
  * 令牌可放 X-Yue2-Token、Authorization: Bearer、?token= 或 cookie（?token= 会换成 cookie）。
  * 不做用户体系、不做权限分级——它只补上"直连 7863 无鉴权"这一层。
"""

from __future__ import annotations

import hmac
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

COOKIE_NAME = "yue2_token"
COOKIE_MAX_AGE = 30 * 24 * 3600

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def client_is_loopback(host: str | None) -> bool:
    h = (host or "").strip().lower().strip("[]")
    if h.startswith("::ffff:"):
        h = h[7:]
    return h in _LOOPBACK or h.startswith("127.")


def token_matches(candidate: str | None, expected: str | None) -> bool:
    if not candidate or not expected:
        return False
    try:
        return hmac.compare_digest(str(candidate), str(expected))
    except Exception:
        return False


def request_has_token(request, expected: str) -> bool:
    header = request.headers.get("x-yue2-token", "")
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    cookie = request.cookies.get(COOKIE_NAME, "")
    query = request.query_params.get("token", "")
    return any(token_matches(v, expected) for v in (header, bearer, cookie, query))


def token_from_query(request, expected: str) -> bool:
    return token_matches(request.query_params.get("token"), expected)


def strip_token(url: str) -> str:
    """把 URL 里的 token 参数去掉；其余参数与顺序保持不变。"""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "token"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
