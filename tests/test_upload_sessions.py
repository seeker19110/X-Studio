import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from studio.events import MetadataPackage
from studio.platform import PlatformError, Tokens, TokenStore, YouTubePlatform
from studio.upload_sessions import UploadSessions

META = MetadataPackage(video_id='V1', title='Video', description='Nội dung', tags=[])
LOCATION = 'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=private-session'


def platform(tmp_path, sessions, fetcher):
    tokens = TokenStore(tmp_path / 'tokens.json')
    tokens.save(Tokens(access_token='access', refresh_token='refresh', expiry=(datetime.now(UTC)+timedelta(hours=1)).isoformat()))
    return YouTubePlatform(tokens, fetcher=fetcher, sleep=lambda _:None, sessions=sessions)


def test_restart_probes_existing_session_and_resumes_confirmed_offset(tmp_path):
    db = tmp_path / 'studio.sqlite'; file = tmp_path / 'final.mp4'; file.write_bytes(b'0123456789')
    def crash(method, url, headers, body):
        if method == 'POST': return 200, {'location':LOCATION}, b''
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        platform(tmp_path, UploadSessions(db), crash).upload_video(file, META, operation_id='V1', authorization='gate1')
    calls = []
    def recover(method, url, headers, body):
        calls.append((method, url, headers, body))
        assert method == 'PUT' and url == LOCATION
        if not body: return 308, {'range':'bytes=0-3'}, b''
        assert body == b'456789' and headers['Content-Range'] == 'bytes 4-9/10'
        return 201, {}, json.dumps({'id':'video-real'}).encode()
    result = platform(tmp_path, UploadSessions(db), recover).upload_video(file, META, operation_id='V1', authorization='gate1')
    assert result.platform_ref == 'video-real' and len(calls) == 2
    again = platform(tmp_path, UploadSessions(db), Mock(side_effect=AssertionError('completed upload must not repeat')))
    assert again.upload_video(file, META, operation_id='V1', authorization='gate1').platform_ref == 'video-real'
    assert 'private-session' not in result.evidence


def test_completed_final_chunk_without_local_result_is_reconciled(tmp_path):
    db = tmp_path / 'studio.sqlite'; file = tmp_path / 'final.mp4'; file.write_bytes(b'video')
    def lost(method, url, headers, body):
        if method == 'POST': return 200, {'location':LOCATION}, b''
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): platform(tmp_path, UploadSessions(db), lost).upload_video(file, META, operation_id='V1')
    recover = Mock(return_value=(200, {}, b'{"id":"already-complete"}'))
    result = platform(tmp_path, UploadSessions(db), recover).upload_video(file, META, operation_id='V1')
    assert result.platform_ref == 'already-complete' and recover.call_count == 1


def test_upload_identity_or_authorization_changes_hold_existing_session(tmp_path):
    db = tmp_path / 'studio.sqlite'; file = tmp_path / 'final.mp4'; file.write_bytes(b'video')
    def lost(method, url, headers, body):
        if method == 'POST': return 200, {'location':LOCATION}, b''
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): platform(tmp_path, UploadSessions(db), lost).upload_video(file, META, operation_id='V1', authorization='old')
    network = Mock(side_effect=AssertionError('changed gate must not upload'))
    with pytest.raises(PlatformError): platform(tmp_path, UploadSessions(db), network).upload_video(file, META, operation_id='V1', authorization='new')
    file.write_bytes(b'changed')
    with pytest.raises(PlatformError): platform(tmp_path, UploadSessions(db), network).upload_video(file, META, operation_id='V1', authorization='old')


def interrupted(tmp_path):
    sessions=UploadSessions(tmp_path/'db'); file=tmp_path/'final.mp4';file.write_bytes(b'0123456789')
    def crash(method,url,headers,body):
        if method=='POST': return 200,{'location':LOCATION},b''
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): platform(tmp_path,sessions,crash).upload_video(file,META,operation_id='V1')
    return sessions,file


def test_expired_session_opens_new_session_and_persists_progress(tmp_path,monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr('studio.platform.UPLOAD_CHUNK_BYTES',4)
        sessions,file=interrupted(tmp_path)
    calls=[]
    def recover(method,url,headers,body):
        calls.append(method)
        if len(calls)==1: return 404,{},b'{}'
        if method=='POST': return 200,{'location':LOCATION},b''
        return 201,{},b'{"id":"new-session"}'
    assert platform(tmp_path,sessions,recover).upload_video(file,META,operation_id='V1').platform_ref=='new-session'
    assert calls==['PUT','POST','PUT'] and sessions.get('V1')['offset']==10


@pytest.mark.parametrize('value',['bad','bytes=0-9'])
def test_invalid_server_range_holds_without_restarting(tmp_path,value):
    sessions,file=interrupted(tmp_path)
    fetcher=Mock(return_value=(308,{'range':value},b''))
    with pytest.raises(PlatformError): platform(tmp_path,sessions,fetcher).upload_video(file,META,operation_id='V1')
    assert fetcher.call_count==1


@pytest.mark.parametrize('body',[b'bad',b'[]',b'{}',b'{"id":"x","status":[]}'])
def test_malformed_completion_keeps_session_for_reconciliation(tmp_path,body):
    sessions,file=interrupted(tmp_path)
    with pytest.raises(PlatformError): platform(tmp_path,sessions,Mock(return_value=(200,{},body))).upload_video(file,META,operation_id='V1')
    assert not sessions.get('V1').get('result')


def test_persisted_untrusted_origin_never_receives_oauth(tmp_path):
    sessions,file=interrupted(tmp_path)
    saved=sessions.get('V1');saved['location']='https://attacker.invalid/session';sessions.save('V1',saved)
    network=Mock(side_effect=AssertionError('must not send OAuth'))
    with pytest.raises(PlatformError): platform(tmp_path,sessions,network).upload_video(file,META,operation_id='V1')


def test_upload_session_errors_do_not_echo_private_uri(tmp_path):
    sessions,file=interrupted(tmp_path)
    network=Mock(return_value=(403,{},json.dumps({'error':{'message':'refused '+LOCATION}}).encode()))
    with pytest.raises(PlatformError) as caught:
        platform(tmp_path,sessions,network).upload_video(file,META,operation_id='V1')
    assert 'private-session' not in str(caught.value) and caught.value.status==403


def test_chunk_acknowledgements_are_durable_before_next_chunk(tmp_path,monkeypatch):
    monkeypatch.setattr('studio.platform.UPLOAD_CHUNK_BYTES',4)
    sessions=UploadSessions(tmp_path/'db');file=tmp_path/'final.mp4';file.write_bytes(b'12345678')
    def network(method,url,headers,body):
        if method=='POST':return 200,{'location':LOCATION},b''
        if body==b'1234':return 308,{'range':'bytes=0-3'},b''
        assert sessions.get('V1')['offset']==4
        return 201,{},b'{"id":"complete"}'
    result=platform(tmp_path,sessions,network).upload_video(file,META,operation_id='V1')
    assert result.platform_ref=='complete' and sessions.get('V1')['offset']==8


@pytest.mark.parametrize('location',[None,'https://attacker.invalid/x','https://www.googleapis.com:bad/x'])
def test_new_upload_session_requires_trusted_location(tmp_path,location):
    sessions=UploadSessions(tmp_path/'db');file=tmp_path/'final.mp4';file.write_bytes(b'video')
    fetcher=Mock(return_value=(200,{'location':location} if location else {},b''))
    with pytest.raises(PlatformError):platform(tmp_path,sessions,fetcher).upload_video(file,META,operation_id='V1')
    assert fetcher.call_count==1 and sessions.get('V1') is None


def test_orchestrator_pins_upload_operation_to_approved_gate_generation(tmp_path,monkeypatch):
    from studio.factory import Factory
    from studio.media import MediaConfig
    from studio.platform import UploadResult
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    factory=Factory(tmp_path/'db',MediaConfig(output_dir=tmp_path/'out',production={'automation':'production'}),demo=True)
    try:
        factory.command('create',{'title':'Upload có checkpoint','target_minutes':.2})
        for _ in range(40):factory.cycle()
        vid=factory.snapshot()['projects'][0]['id'];gate=factory.orch.gate.pending['PUB-'+vid]
        youtube=platform(tmp_path,UploadSessions(tmp_path/'db'),Mock(side_effect=AssertionError('no network')))
        youtube.upload_video=Mock(return_value=UploadResult('remote','https://youtu.be/remote','private'))
        youtube.set_thumbnail=Mock(return_value='thumb');youtube.schedule=Mock(return_value='scheduled');youtube.upload_captions=Mock(return_value='captions')
        factory.orch.platform=youtube
        factory.command('gate',{'subject_id':'PUB-'+vid,'decision':'approve','request_at':gate.created_at.isoformat()})
        for _ in range(20):factory.cycle()
        assert youtube.upload_video.call_count==1
        assert youtube.upload_video.call_args.kwargs['operation_id']==vid
        assert youtube.upload_video.call_args.kwargs['authorization']==gate.created_at.isoformat()
    finally:factory.close()


def test_expired_session_after_final_attempt_holds_for_reconciliation(tmp_path):
    sessions,file=interrupted(tmp_path)
    network=Mock(return_value=(404,{},b'{}'))
    with pytest.raises(PlatformError,match='đối chiếu'):
        platform(tmp_path,sessions,network).upload_video(file,META,operation_id='V1')
    assert network.call_count==1 and sessions.get('V1')['state']=='needs_reconcile'
