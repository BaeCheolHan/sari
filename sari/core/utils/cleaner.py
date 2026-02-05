import re

class KoreanCleaner:
    """
    Normalizes Korean, English, and Numbers for better FTS matching.
    """
    KOREAN_NUMBERS = {
        '0': '영', '1': '일', '2': '이', '3': '삼', '4': '사',
        '5': '오', '6': '육', '7': '칠', '8': '팔', '9': '구'
    }
    
    @staticmethod
    def normalize(text: str) -> str:
        if not text:
            return ""
        
        # 1. Lowercase English
        text = text.lower()
        
        # 2. Map digits to Korean pronunciation
        def _num_repl(m):
            n = m.group(0)
            if len(n) > 10: # Don't normalize very long numeric strings (hashes etc)
                return n
            kor = "".join(KoreanCleaner.KOREAN_NUMBERS.get(c, c) for c in n)
            return f" {n} {kor} "
            
        text = re.sub(r'\d+', _num_repl, text)
        
        # 3. Basic whitespace normalization
        text = " ".join(text.split())
        
        return text

def clean_for_fts(text: str) -> str:
    return KoreanCleaner.normalize(text)
