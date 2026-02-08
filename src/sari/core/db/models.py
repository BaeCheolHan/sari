from peewee import *
import time
import json

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
    config_json = TextField(default="{}")
    created_ts = IntegerField(default=lambda: int(time.time()))
    updated_ts = IntegerField(default=lambda: int(time.time()))

    class Meta:
        table_name = 'roots'

class File(BaseModel):
    path = CharField(primary_key=True)
    rel_path = TextField()
    root = ForeignKeyField(Root, backref='files', column_name='root_id', on_delete='CASCADE')
    repo = CharField(index=True)
    mtime = BigIntegerField()
    size = BigIntegerField()
    content = BlobField()
    content_hash = CharField(index=True, default="")
    fts_content = TextField(default="")
    last_seen_ts = IntegerField(default=0)
    deleted_ts = IntegerField(default=0)
    parse_status = CharField(default="none")
    parse_reason = TextField(default="none")
    ast_status = CharField(default="none")
    ast_reason = TextField(default="none")
    is_binary = IntegerField(default=0)
    is_minified = IntegerField(default=0)
    sampled = IntegerField(default=0)
    content_bytes = BigIntegerField(default=0)
    metadata_json = TextField(default="{}")

    class Meta:
        table_name = 'files'

class Symbol(BaseModel):
    symbol_id = CharField(null=True)
    path = CharField()
    root_id = CharField()
    name = CharField(index=True)
    kind = CharField()
    line = IntegerField()
    end_line = IntegerField()
    content = TextField()
    parent_name = CharField(default="")
    metadata = TextField(default="{}")
    docstring = TextField(default="")
    qualname = TextField(default="")

    class Meta:
        table_name = 'symbols'
        primary_key = CompositeKey('root_id', 'path', 'name', 'line')

class Relation(BaseModel):
    from_path = CharField()
    from_root_id = CharField()
    from_symbol = CharField()
    from_symbol_id = CharField(default="")
    to_path = CharField()
    to_root_id = CharField()
    to_symbol = CharField()
    to_symbol_id = CharField(default="")
    rel_type = CharField()
    line = IntegerField()
    metadata_json = TextField(default="{}")

    class Meta:
        table_name = 'symbol_relations'
        indexes = (
            (('from_root_id', 'from_path', 'from_symbol', 'to_path', 'to_symbol', 'rel_type', 'line'), True),
        )

class FailedTask(BaseModel):
    path = CharField(primary_key=True)
    root = ForeignKeyField(Root, backref='failed_tasks', column_name='root_id', on_delete='CASCADE')
    attempts = IntegerField(default=0)
    error = TextField()
    ts = IntegerField()
    next_retry = IntegerField()
    metadata_json = TextField(default="{}")

    class Meta:
        table_name = 'failed_tasks'

class Context(BaseModel):
    id = AutoField()
    topic = CharField(unique=True)
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
    id = AutoField()
    tag = CharField()
    path = CharField()
    root = ForeignKeyField(Root, backref='snippets', column_name='root_id', on_delete='CASCADE')
    start_line = IntegerField()
    end_line = IntegerField()
    content = TextField()
    content_hash = CharField()
    anchor_before = TextField(default="")
    anchor_after = TextField(default="")
    repo = CharField(default="")
    note = TextField(default="")
    commit_hash = CharField(default="")
    created_ts = IntegerField()
    updated_ts = IntegerField()
    metadata_json = TextField(default="{}")

    class Meta:
        table_name = 'snippets'
        indexes = (
            (('tag', 'root_id', 'path', 'start_line', 'end_line'), True),
        )
