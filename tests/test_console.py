"""Exercise the real HTTP boundary: authentication, CSRF, commands and streaming."""
import http.client
import json
import threading
from unittest.mock import Mock

import pytest

from studio.console import ConsoleServer


@pytest.fixture
def http_console(tmp_path):
    factory = Mock(); factory.snapshot.return_value = {"projects": []}
    factory.command.return_value = "accepted"
    file = tmp_path / "video.mp4"; file.write_bytes(b"0123456789")
    factory.media_path.side_effect = lambda id: file if id == "known" else None
    server = ConsoleServer(("127.0.0.1", 0), factory, "test-token")
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()

    def request(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        conn.request(method, path, json.dumps(body) if body is not None else None, headers or {})
        response = conn.getresponse(); result = response.status, dict(response.getheaders()), response.read()
        conn.close(); return result
    yield request, factory, file, server
    server.shutdown(); server.server_close(); worker.join()


def test_authentication_origin_csrf_and_command_validation(http_console):
    request, factory, _, _ = http_console
    assert request("GET", "/")[0] == 200
    assert request("GET", "/app.js")[0] == 200
    assert request("GET", "/style.css")[0] == 200
    assert request("GET", "/api/state")[0] == 401
    assert request("GET", "/api/state", headers={"Host": "evil.test"})[0] == 403
    assert request("POST", "/api/login", {"token":"test-token"}, {"Origin":"http://evil.test"})[0] == 403
    assert request("POST", "/api/login", {"token":"wrong"})[0] == 401
    status, headers, _ = request("POST", "/api/login", {"token":"test-token"})
    assert status == 200 and "HttpOnly" in headers["Set-Cookie"]
    auth = {"Cookie": headers["Set-Cookie"].split(";")[0]}
    assert request("GET", "/api/state", headers=auth)[0] == 200
    assert request("POST", "/api/commands", {"kind":"pause"})[0] == 401
    assert request("POST", "/api/commands", {"kind":"pause"}, auth)[0] == 403
    auth["X-Studio-Request"] = "1"
    assert request("POST", "/api/commands", {"kind":"pause"}, auth)[0] == 202
    factory.command.assert_called_with("pause", {})
    assert request("POST", "/unknown", {}, auth)[0] == 404
    assert request("GET", "/unknown", headers=auth)[0] == 404
    assert request("GET", "/media/unknown", headers=auth)[0] == 404
    assert request("POST", "/api/commands", [], auth)[0] == 400
    assert request("POST", "/api/commands", None, auth)[0] == 400
    assert request("POST", "/api/commands", {}, auth)[0] == 400


def test_ideas_and_doctor_http_require_auth_and_revision_conflicts_use_409(http_console):
    from studio.storage import ConflictError
    request, factory, _, _ = http_console
    factory.ideas.snapshot.return_value = {'ideas': [], 'database_id':'db1'}
    factory.ideas.import_items.return_value = {'ideas': [{'id':'a','title':'Việt'}]}
    factory.ideas.apply.return_value = {'ideas': [{'id':'a','revision':1}]}
    factory.doctor.return_value = {'ok': True, 'checks':[]}
    auth = {'Authorization':'Bearer test-token', 'X-Studio-Request':'1'}
    for endpoint in ('/api/ideas', '/api/doctor?flow=website'):
        assert request('GET', endpoint)[0] == 401
        assert request('GET', endpoint, headers=auth)[0] == 200
    factory.doctor.assert_called_with('website')
    assert request('POST', '/api/ideas', {'action':'import', 'ideas':[]})[0] == 401
    assert request('POST', '/api/ideas', {'action':'import', 'ideas':[]}, {'Authorization':'Bearer test-token'})[0] == 403
    assert request('POST', '/api/ideas', {'action':'import', 'ideas':[]}, auth)[0] == 200
    assert request('POST', '/api/ideas', {'action':'apply', 'changes':[]}, auth)[0] == 200
    assert request('POST', '/api/ideas', {'action':'unknown'}, auth)[0] == 400
    factory.ideas.apply.side_effect = ConflictError('stale')
    assert request('POST', '/api/ideas', {'action':'apply', 'changes':[]}, auth)[0] == 409


def test_media_ranges_and_untrusted_file_types(http_console):
    request, _, file, _ = http_console
    auth = {"Authorization":"Bearer test-token"}
    assert request("GET", "/media/known", headers=auth)[2] == b"0123456789"
    status, headers, body = request("GET", "/media/known", headers={**auth, "Range":"bytes=2-5"})
    assert status == 206 and body == b"2345" and headers["Content-Range"] == "bytes 2-5/10"
    assert request("GET", "/media/known", headers={**auth, "Range":"bytes=8-"})[2] == b"89"
    for value in ["invalid", "bytes=50-", "bytes=5-2"]:
        assert request("GET", "/media/known", headers={**auth, "Range":value})[0] == 416
    file.rename(file.with_suffix(".html"))
    http_console[1].media_path.side_effect = None
    http_console[1].media_path.return_value = file.with_suffix(".html")
    status, headers, _ = request("GET", "/media/known", headers=auth)
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["Content-Disposition"] == "attachment"


def test_console_main_closes_worker_and_lease(tmp_path, monkeypatch, capsys):
    from studio import console
    factory = Mock(); server = Mock(server_port=9876)
    server.serve_forever.side_effect = KeyboardInterrupt
    monkeypatch.setattr(console, "Factory", Mock(return_value=factory))
    monkeypatch.setattr(console, "ConsoleServer", Mock(return_value=server))
    monkeypatch.delenv("STUDIO_CONSOLE_TOKEN", raising=False)
    assert console.main(["--demo", "--db", str(tmp_path / "test.sqlite")]) == 0
    factory.close.assert_called_once(); server.server_close.assert_called_once()
    assert "Access code:" in capsys.readouterr().out


@pytest.mark.parametrize('headers', [
    {'Authorization': 'Bearer mã-sai'},
    {'Cookie': 'studio_session="mã-sai"'},
])
def test_non_ascii_credentials_are_rejected_without_dropping_connection(http_console, headers):
    request, _, _, _ = http_console
    assert request('GET', '/api/state', headers=headers)[0] == 401


def test_non_ascii_login_token_returns_authentication_error(http_console):
    request, _, _, _ = http_console
    assert request('POST', '/api/login', {'token': 'mã-sai'})[0] == 401


def test_console_bad_cookie_and_bind_failure(http_console,tmp_path,monkeypatch):
    from studio import console
    request,_,_,_=http_console
    monkeypatch.setattr(console,'SimpleCookie',Mock(return_value=Mock(load=Mock(side_effect=ValueError('bad cookie')))))
    assert request('GET','/api/state',headers={'Cookie':'malformed'})[0]==401
    factory=Mock()
    monkeypatch.setattr(console,'Factory',Mock(return_value=factory))
    monkeypatch.setattr(console,'ConsoleServer',Mock(side_effect=OSError('port occupied')))
    monkeypatch.setattr(console,'load_media_config',Mock(return_value=Mock()))
    monkeypatch.setenv('STUDIO_CONSOLE_TOKEN','test')
    with pytest.raises(OSError): console.main(['--db',str(tmp_path/'test.sqlite')])
    factory.close.assert_called_once()


@pytest.mark.parametrize(('value', 'expected', 'content_range'), [
    ('bytes=-3', b'789', 'bytes 7-9/10'),
    ('bytes=-20', b'0123456789', 'bytes 0-9/10'),
    ('bytes=8-100', b'89', 'bytes 8-9/10'),
])
def test_media_suffix_and_clamped_ranges(http_console, value, expected, content_range):
    request, _, _, _ = http_console
    status, headers, body = request('GET', '/media/known', headers={
        'Authorization': 'Bearer test-token', 'Range': value,
    })
    assert status == 206
    assert body == expected
    assert headers['Content-Length'] == str(len(expected))
    assert headers['Content-Range'] == content_range


@pytest.mark.parametrize('value', ['bytes=-0', 'bytes=-', 'bytes=10-', 'bytes=5-2',
                                  'bytes=0-1,3-4', 'bytes=' + '9' * 5000 + '-'])
def test_media_unsatisfiable_ranges_include_current_size(http_console, value):
    request, _, _, _ = http_console
    status, headers, _ = request('GET', '/media/known', headers={
        'Authorization': 'Bearer test-token', 'Range': value,
    })
    assert status == 416
    assert headers['Content-Range'] == 'bytes */10'


def test_media_empty_and_disappearing_files(http_console):
    request, _, file, _ = http_console
    auth = {'Authorization': 'Bearer test-token'}
    file.write_bytes(b'')
    status, headers, body = request('GET', '/media/known', headers=auth)
    assert status == 200 and body == b'' and headers['Content-Length'] == '0'
    status, headers, _ = request('GET', '/media/known', headers={**auth, 'Range': 'bytes=-1'})
    assert status == 416 and headers['Content-Range'] == 'bytes */0'
    file.unlink()
    assert request('GET', '/media/known', headers=auth)[0] == 404


def test_media_length_matches_file_opened_after_atomic_replacement(http_console, monkeypatch):
    from pathlib import Path

    request, _, file, _ = http_console
    replacement = file.with_suffix('.new')
    replacement.write_bytes(b'new')
    original_open = Path.open

    def replace_before_open(path, *args, **kwargs):
        if path == file and replacement.exists():
            replacement.replace(file)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', replace_before_open)
    status, headers, body = request('GET', '/media/known', headers={'Authorization': 'Bearer test-token'})
    assert status == 200 and body == b'new'
    assert headers['Content-Length'] == '3'


def test_project_resources_library_and_raw_upload_routes_enforce_http_boundary(http_console):
    request,factory,_,server=http_console
    auth={'Authorization':'Bearer test-token','X-Studio-Request':'1'}
    factory.projects_page.return_value={'projects':[],'total':0}
    factory.project_detail.return_value={'id':'V1'}
    factory.resources.return_value={'inventory':{}}
    factory.library.snapshot.return_value={'revision':0,'assets':[]}
    factory.library.save.return_value={'revision':1}
    factory.library.upload.return_value={'assets':[]}
    assert request('GET','/api/projects?limit=10&offset=2',headers=auth)[0]==200
    factory.projects_page.assert_called_with(10,2)
    assert request('GET','/api/projects?limit=invalid',headers=auth)[0]==400
    assert request('GET','/api/projects/V1',headers=auth)[0]==200
    factory.project_detail.side_effect=ValueError('missing')
    assert request('GET','/api/projects/unknown',headers=auth)[0]==404
    assert request('GET','/api/library',headers=auth)[0]==200
    factory.library.snapshot.side_effect=ValueError('invalid catalog')
    assert request('GET','/api/library',headers=auth)[0]==400
    assert request('POST','/api/library',{'revision':0,'asset':{}},auth)[0]==200
    assert request('GET','/api/resources?max_bytes=5',headers=auth)[0]==200
    assert request('GET','/api/resources?max_bytes=no',headers=auth)[0]==400
    factory.doctor.side_effect=ValueError('invalid flow')
    assert request('GET','/api/doctor?flow=unknown',headers=auth)[0]==400
    assert request('POST','/api/library/upload?name=file.png',{})[0]==401
    assert request('POST','/api/library/upload?name=file.png',{}, {'Authorization':'Bearer test-token'})[0]==403
    # Raw bytes, including arbitrary binary, must reach the uploader without a JSON round trip.
    def upload(name,stream,length):
        assert name=='ảnh.png' and stream.read(length)==b'\x00\xff\x01'
        return {'assets':[]}
    factory.library.upload.side_effect=upload
    conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
    conn.request('POST','/api/library/upload?name=%E1%BA%A3nh.png',b'\x00\xff\x01',auth)
    response=conn.getresponse();assert response.status==200;response.read();conn.close()
    assert request('GET','/api/state',headers={**auth,'X-Studio-Overview':'1'})[0]==200
    factory.snapshot.assert_called_with(lean=True)
