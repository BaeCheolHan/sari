from peewee import *
import time
import json
from sari.core.models import FileDTO, IndexingResult, SymbolDTO, SnippetDTO, ContextDTO, FILE_COLUMNS, _to_dict

db_proxy = Proxy()

class BaseModel(Model):
    class Meta:
        database = db_proxy

class Root(BaseModel):
    """워크스페이스 루트 정보를 나타내는 모델입니다."""
    root_id = CharField(primary_key=True)      # 루트 고유 ID (해시 등)
    root_path = TextField()                   # 원본 경로
    real_path = TextField()                   # 실제 물리 경로
    label = CharField(default="")             # 표시 이름
    state = TextField(default="ready")        # 상태 (ready, indexing 등)
    file_count = IntegerField(default=0)      # 인덱싱된 파일 수
    symbol_count = IntegerField(default=0)    # 추출된 심볼 수
    config_json = TextField(default="{}")     # 개별 설정 (JSON)
    created_ts = IntegerField(default=lambda: int(time.time())) # 생성 시간
    updated_ts = IntegerField(default=lambda: int(time.time())) # 수정 시간

    class Meta:
        table_name = 'roots'

class File(BaseModel):
    """인덱싱된 개별 파일의 정보를 나타내는 모델입니다."""
    # Compatible Order: 0:path, 1:rel_path, 2:root_id, 3:repo, ...
    path = CharField(primary_key=True)        # 파일 절대 경로
    rel_path = TextField()                    # 루트 기준 상대 경로
    root = ForeignKeyField(Root, backref='files', column_name='root_id', on_delete='CASCADE') # 소속 루트
    repo = CharField(index=True)              # 저장소 이름 (옵션)
    mtime = BigIntegerField()                 # 파일 수정 시간
    size = BigIntegerField()                  # 파일 크기
    content = BlobField(null=True)            # 파일 내용 (압축 가능)
    hash = CharField(index=True, default="")  # 내용 해시 (변경 감지)
    fts_content = TextField(default="")       # 검색용 텍스트 컨텐츠
    last_seen_ts = IntegerField(default=0)    # 최근 발견 시간
    deleted_ts = IntegerField(default=0)      # 삭제 시간 (Soft delete, 0이면 정상)
    status = CharField(default="ok")          # 상태 (ok, error 등)
    error = TextField(null=True)              # 에러 메시지
    parse_status = CharField(default="ok")    # 파싱 상태
    parse_error = TextField(null=True)        # 파싱 오류
    ast_status = CharField(default="none")    # AST 분석 상태
    ast_reason = TextField(default="none")    # 상태 사유
    is_binary = IntegerField(default=0)       # 이진 파일 여부
    is_minified = IntegerField(default=0)     # 압축 파일 여부
    metadata_json = TextField(default="{}")   # 추가 메타데이터

    class Meta:
        table_name = 'files'

class Symbol(BaseModel):
    """추출된 코드 심볼(함수, 클래스 등)을 나타내는 모델입니다."""
    symbol_id = CharField(primary_key=True)   # 심볼 고유 ID
    path = CharField()                        # 소속 파일 경로
    root_id = CharField()                     # 소속 루트 ID
    name = CharField(index=True)              # 심볼 명칭
    kind = CharField()                        # 심볼 종류 (function, class 등)
    line = IntegerField()                     # 시작 라인
    end_line = IntegerField()                 # 종료 라인
    content = TextField()                     # 심볼 코드 컨텐츠
    parent = CharField(default="", column_name='parent') # 부모 심볼명
    meta_json = TextField(default="{}", column_name='meta_json') # 가변 메타데이터
    doc_comment = TextField(default="", column_name='doc_comment') # 문서화 주석
    qualname = CharField(default="")          # 전체 이름 (Qualified Name)
    importance_score = FloatField(default=0.0) # 중요도 점수

    class Meta:
        table_name = 'symbols'

class Relation(BaseModel):
    """심볼 간의 관계(호출, 상속 등)를 나타내는 모델입니다."""
    src_sid = CharField()                     # 원본(출발) 심볼 ID
    dst_sid = CharField()                     # 대상(도착) 심볼 ID
    kind = CharField()                        # 관계 종류 (call, inheritance 등)
    meta_json = TextField(default="{}")       # 관계 메타데이터

    class Meta:
        table_name = 'relations'
        primary_key = CompositeKey('src_sid', 'dst_sid', 'kind')

class FailedTask(BaseModel):
    """실패한 작업(재시도 대기)을 나타내는 모델입니다."""
    path = CharField(primary_key=True)        # 실패 경로
    root = ForeignKeyField(Root, backref='failed_tasks', column_name='root_id', on_delete='CASCADE') # 소속 루트
    attempts = IntegerField(default=0)        # 시도 횟수
    error = TextField()                       # 에러 내용
    ts = IntegerField()                       # 발생 시각
    next_retry = IntegerField()               # 다음 시도 시각
    meta_json = TextField(default="{}", column_name='meta_json') # 작업 메타데이터

    class Meta:
        table_name = 'failed_tasks'

class Context(BaseModel):
    """주제 기반 분석 맥락을 나타내는 모델입니다."""
    topic = CharField(primary_key=True)       # 키워드/주제
    content = TextField()                     # 맥락 본문
    tags_json = TextField(default="[]")       # 태그 목록
    related_files_json = TextField(default="[]") # 연관 파일 목록
    source = CharField(default="")            # 데이터 출처
    valid_from = IntegerField(default=0)      # 유효 시작일
    valid_until = IntegerField(default=0)     # 유효 종료일
    deprecated = IntegerField(default=0)      # 폐기 여부
    created_ts = IntegerField()               # 생성 시각
    updated_ts = IntegerField()               # 수정 시각

    class Meta:
        table_name = 'contexts'

class Snippet(BaseModel):
    """코드 스니펫과 해당 위치/메모를 기록하는 모델입니다."""
    tag = CharField(primary_key=True)         # 스니펫 분류 태그
    path = CharField()                        # 대상 파일 경로
    start_line = IntegerField()               # 시작 라인
    end_line = IntegerField()                 # 종료 라인
    note = TextField(default="")              # 관련 메모
    commit_hash = CharField(default="")       # 생성 시점의 커밋 해시
    created_ts = IntegerField()               # 생성 시각

    class Meta:
        table_name = 'snippets'
