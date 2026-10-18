import sqlite3

import pytest

from studio import storage


def test_migration_preserves_legacy_rows_and_database_identity(tmp_path):
    db = tmp_path / 'studio.sqlite'
    with storage.connect(db) as con:
        con.execute('CREATE TABLE website_jobs(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        con.execute("INSERT INTO website_jobs VALUES ('legacy','{}')")
    storage.migrate(db)
    first = storage.IdeaStore(db).snapshot()
    storage.migrate(db)
    assert storage.IdeaStore(db).snapshot() == first
    with storage.connect(db) as con:
        assert con.execute('SELECT id FROM website_jobs').fetchone()[0] == 'legacy'
        assert con.execute("SELECT value FROM studio_meta WHERE key='schema_version'").fetchone()[0] == str(len(storage.MIGRATIONS))


def test_migration_rolls_back_failed_upgrade_and_rejects_future_database(tmp_path, monkeypatch):
    db = tmp_path / 'studio.sqlite'
    def broken(con):
        con.execute('CREATE TABLE unfinished(id INTEGER)')
        raise sqlite3.OperationalError('injected migration failure')
    with monkeypatch.context() as patch:
        patch.setattr(storage, 'MIGRATIONS', (broken,))
        with pytest.raises(sqlite3.OperationalError): storage.migrate(db)
    with storage.connect(db) as con:
        assert not con.execute("SELECT name FROM sqlite_master WHERE name='unfinished'").fetchall()
    storage.migrate(db)
    with storage.connect(db) as con:
        con.execute("UPDATE studio_meta SET value='99' WHERE key='schema_version'")
    with pytest.raises(ValueError, match='version'): storage.migrate(db)


def change(id='a', revision=0, title='Ý tưởng Việt'):
    return {'id': id, 'revision': revision, 'idea': {'id': id, 'title': title}}


def test_ideas_persist_across_clients_and_detect_stale_writes(tmp_path):
    db = tmp_path / 'studio.sqlite'
    a, b = storage.IdeaStore(db), storage.IdeaStore(db)
    assert a.apply([change()])['ideas'][0]['revision'] == 1
    b.apply([change(revision=1, title='Đã sửa')])
    with pytest.raises(storage.ConflictError): a.apply([change(revision=1)])
    assert storage.IdeaStore(db).snapshot()['ideas'][0]['title'] == 'Đã sửa'


def test_import_is_idempotent_and_cannot_resurrect_deleted_ideas(tmp_path):
    store = storage.IdeaStore(tmp_path / 'studio.sqlite')
    legacy = [{'id': 'a', 'title': 'Cũ', 'hook': 'Dấu tiếng Việt', 'created_at': '2026-10-16T01:00:00Z'}]
    store.import_items(legacy); store.import_items(legacy)
    assert len(store.snapshot()['ideas']) == 1
    store.apply([change(revision=1, title='Mới')])
    store.import_items(legacy)
    assert store.snapshot()['ideas'][0]['title'] == 'Mới'
    deleted = store.apply([{'id': 'a', 'revision': 2, 'idea': None}])
    store.import_items(legacy)
    assert store.snapshot()['ideas'] == []
    assert deleted['revisions']['a'] == 3
    store.apply([change(revision=3, title='Khôi phục')])
    assert store.snapshot()['ideas'][0]['revision'] == 4


def test_idea_batch_is_atomic_and_invalid_import_keeps_every_existing_row(tmp_path):
    store = storage.IdeaStore(tmp_path / 'studio.sqlite')
    store.apply([change()])
    with pytest.raises(storage.ConflictError): store.apply([change('b'), change(revision=0)])
    assert [i['id'] for i in store.snapshot()['ideas']] == ['a']
    for operations in ([change(), change()], [dict(change(), id='different')], [{'id':'a', 'revision':True}], []):
        with pytest.raises(ValueError): store.apply(operations)
    with pytest.raises(ValueError): store.import_items([{'id':'b', 'title':'Okay'}, {'id':'c', 'title':''}])
    assert len(store.snapshot()['ideas']) == 1


def test_idea_limit_is_enforced_on_server_and_transaction_closes(tmp_path):
    store = storage.IdeaStore(tmp_path / 'studio.sqlite')
    store.import_items([{'id': str(i), 'title': str(i)} for i in range(200)])
    with pytest.raises(ValueError, match='200'): store.apply([change()])
    with storage.connect(store.db) as con: con.execute('SELECT 1')
    with pytest.raises(sqlite3.ProgrammingError): con.execute('SELECT 1')


def test_conversion_commits_command_and_idea_together_and_rejects_duplicate(tmp_path):
    store = storage.IdeaStore(tmp_path / 'studio.sqlite')
    store.apply([change()])
    result = store.convert('a', 1, {'title':'Dự án'})
    assert result['ideas'][0]['command_id'] == result['id']
    assert result['ideas'][0]['revision'] == 2
    for id, revision in [('a',1), ('a',2), ('missing',0)]:
        with pytest.raises(storage.ConflictError): store.convert(id, revision, {'title':'Trùng'})
    with storage.connect(store.db) as con:
        assert con.execute('SELECT count(*) FROM factory_commands').fetchone()[0] == 1
    for items in ('invalid', [{}] * 201):
        with pytest.raises(ValueError): store.import_items(items)
