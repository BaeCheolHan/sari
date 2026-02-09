from peewee import *
import time
import json
from sari.core.models import FileDTO, IndexingResult, SymbolDTO, SnippetDTO, ContextDTO, FILE_COLUMNS, _to_dict

db_proxy = Proxy()

class BaseModel(Model):
    class Meta:
        database = db_proxy

class Root(BaseModel):
    root_id = CharField(primary_key=True)
    root_path = TextField()
    real_path = TextField()
    label = CharField(default="")
    state = TextField(default="ready")
    file_count = IntegerField(default=0)
    symbol_count = IntegerField(default=0)
    config_json = TextField(default="{}")
    created_ts = IntegerField(default=lambda: int(time.time()))
    updated_ts = IntegerField(default=lambda: int(time.time()))

    class Meta:
        table_name = 'roots'

class File(BaseModel):
    # Compatible Order: 0:path, 1:rel_path, 2:root_id, 3:repo, ...
    path = CharField(primary_key=True)
    rel_path = TextField()
    root = ForeignKeyField(Root, backref='files', column_name='root_id', on_delete='CASCADE')
    repo = CharField(index=True)
    mtime = BigIntegerField()
    size = BigIntegerField()
    content = BlobField(null=True)
    hash = CharField(index=True, default="")
    fts_content = TextField(default="")
    last_seen_ts = IntegerField(default=0)
    deleted_ts = IntegerField(default=0)
    status = CharField(default="ok")
    error = TextField(null=True)
    parse_status = CharField(default="ok")
    parse_error = TextField(null=True)
    ast_status = CharField(default="none")
    ast_reason = TextField(default="none")
    is_binary = IntegerField(default=0)
    is_minified = IntegerField(default=0)
    metadata_json = TextField(default="{}")

    class Meta:
        table_name = 'files'

class Symbol(BaseModel):
    symbol_id = CharField(primary_key=True)
    path = CharField()
    root_id = CharField()
    name = CharField(index=True)
    kind = CharField()
    line = IntegerField()
    end_line = IntegerField()
    content = TextField()
    parent = CharField(default="", column_name='parent')
    meta_json = TextField(default="{}", column_name='meta_json')
    doc_comment = TextField(default="", column_name='doc_comment')
    qualname = CharField(default="")
    importance_score = FloatField(default=0.0)

    class Meta:
        table_name = 'symbols'

class Relation(BaseModel):
    src_sid = CharField()
    dst_sid = CharField()
    kind = CharField()
    meta_json = TextField(default="{}")

    class Meta:
        table_name = 'relations'
        primary_key = CompositeKey('src_sid', 'dst_sid', 'kind')

class FailedTask(BaseModel):
    path = CharField(primary_key=True)
    root = ForeignKeyField(Root, backref='failed_tasks', column_name='root_id', on_delete='CASCADE')
    attempts = IntegerField(default=0)
    error = TextField()
    ts = IntegerField()
    next_retry = IntegerField()
    meta_json = TextField(default="{}", column_name='meta_json')

    class Meta:
        table_name = 'failed_tasks'

class Context(BaseModel):
    topic = CharField(primary_key=True)
    content = TextField()
    tags_json = TextField(default="[]")
    related_files_json = TextField(default="[]")
    source = CharField(default="")
    valid_from = IntegerField(default=0)
    valid_until = IntegerField(default=0)
    deprecated = IntegerField(default=0)
    created_ts = IntegerField()
    updated_ts = IntegerField()

    class Meta:
        table_name = 'contexts'

class Snippet(BaseModel):
    tag = CharField(primary_key=True)
    path = CharField()
    start_line = IntegerField()
    end_line = IntegerField()
    note = TextField(default="")
    commit_hash = CharField(default="")
    created_ts = IntegerField()

    class Meta:
        table_name = 'snippets'
