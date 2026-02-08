import re

_REDACT_ASSIGNMENTS_QUOTED = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api_key|apikey|token|access_token|refresh_token|openai_api_key|aws_secret|database_url)\b(\s*[:=]\s*)(['\"])([^'\"]{0,256})(\3)"
)
_REDACT_ASSIGNMENTS_BARE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api_key|apikey|token|access_token|refresh_token|openai_api_key|aws_secret|database_url)\b(\s*[:=]\s*)([^'\"\s,]{1,256})"
)
_REDACT_AUTH_BEARER = re.compile(r"(?i)\bAuthorization\b\s*:\s*Bearer\s+([a-zA-Z0-9._~+/-]{1,512})")
_REDACT_PRIVATE_KEY = re.compile(
    r"(?is)-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----[\s\S]{1,4096}-----END [A-Z0-9 ]+PRIVATE KEY-----"
)

def _redact(text: str) -> str:
    if not text:
        return text
    text = _REDACT_PRIVATE_KEY.sub("-----BEGIN PRIVATE KEY-----[REDACTED]-----END PRIVATE KEY-----", text)
    text = _REDACT_AUTH_BEARER.sub("Authorization: Bearer ***", text)

    def _replace_quoted(match: re.Match) -> str:
        key, sep, quote = match.group(1), match.group(2), match.group(3)
        return f"{key}{sep}{quote}***{quote}"

    def _replace_bare(match: re.Match) -> str:
        key, sep = match.group(1), match.group(2)
        return f"{key}{sep}***"

    text = _REDACT_ASSIGNMENTS_QUOTED.sub(_replace_quoted, text)
    text = _REDACT_ASSIGNMENTS_BARE.sub(_replace_bare, text)
    return text