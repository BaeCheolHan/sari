import unicodedata

def _normalize_engine_text(text: str) -> str:
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", text)
    norm = norm.lower()
    norm = " ".join(norm.split())
    return norm
