import re

SENSITIVE_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S+|\b(?:sk-|kf_|ms-)[A-Za-z0-9_-]{12,}|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?:password|passwd|secret|token|api[_ -]?key|密码|密钥)\s*[:=]\s*[^\s,;]+)"
)


def redact_query(query: str) -> str:
    return SENSITIVE_VALUE.sub("[REDACTED]", query)[:500]
