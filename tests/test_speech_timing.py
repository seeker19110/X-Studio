import json
from itertools import pairwise
from pathlib import Path

import pytest

from studio.media import EdgeTTS, MediaConfig, MediaResult, make_media
from studio.speech import validate_words


def test_word_timing_rejects_invalid_or_nonmonotonic_provider_data():
    valid = [{'text':'Xin','start':0.,'duration':.3},{'text':'chào','start':.4,'duration':.4}]
    assert validate_words(valid,1) == valid
    for words in ([], [{'text':'x','start':float('nan'),'duration':1}],
                  [{'text':'x','start':1,'duration':2}], list(reversed(valid))):
        with pytest.raises(ValueError): validate_words(words,1)


def test_edge_provider_keeps_measured_word_boundaries_and_uses_no_api_key(tmp_path,monkeypatch):
    from studio import speech
    selected_voices=[]
    async def generate(text,voice,path,rate='+0%'):
        selected_voices.append((voice,rate))
        path.write_bytes(b'mp3 fixture'); return [{'text':'Xin chào','start':.1,'duration':.8}]
    monkeypatch.setattr(speech,'edge_audio',generate)
    monkeypatch.setattr('studio.media.probe_duration',lambda _:1.)
    cfg = MediaConfig(tts={'provider':'edge','voice':'vi-VN-HoaiMyNeural','voices':{'onyx':'vi-VN-NamMinhNeural'}})
    provider = EdgeTTS(cfg)
    result = provider.synthesize('Xin chào',{},tmp_path / 'audio.wav')
    assert result.path.suffix == '.mp3' and result.words[0]['start'] == .1
    provider.synthesize('Xin chào',{'voice_id':'onyx','pace':'fast'},tmp_path/'alternate.wav')
    assert selected_voices==[('vi-VN-HoaiMyNeural','+0%'),('vi-VN-NamMinhNeural','+15%')]
    assert make_media(cfg).tts.__class__ is EdgeTTS
    monkeypatch.setattr('studio.media.probe_duration',lambda _:None)
    with pytest.raises(ValueError): provider.synthesize('Xin chào',{},tmp_path / 'bad.wav')


def test_main_renderer_uses_provider_timings_and_falls_back_for_missing_or_damaged_sidecar(tmp_path,monkeypatch):
    from studio.bus import InMemoryBus
    from studio.events import Scene, SceneManifest
    from studio.renderer import Renderer
    cfg = MediaConfig(output_dir=tmp_path)
    suite = make_media(cfg)
    def generate(text,voice,path):
        path.write_bytes(b'audio fixture')
        return MediaResult(path,'test','timed',2.,words=[{'text':'Xin chào','start':.5,'duration':.7}],timing_source='provider')
    monkeypatch.setattr(suite.tts,'synthesize',generate)
    renderer = Renderer(InMemoryBus(),suite)
    manifest = SceneManifest(video_id='V1',scenes=[Scene(scene_id='S1',order=0,narration='Xin chào',visual_prompt='Hình')])
    renderer.render(manifest)
    cue = renderer.caption_cues(manifest)[0]
    assert cue.start == .5 and cue.end <= 1.4
    sidecar = manifest.scenes[0].asset_refs['scene_timing']
    data = json.loads(Path(sidecar).read_text()); data['sha256'] = 'changed'
    Path(sidecar).write_text(json.dumps(data))
    assert renderer.caption_cues(manifest)[0].start == 0


def test_word_timing_missing_fields_are_actionable():
    with pytest.raises(ValueError,match='Mốc từ'):validate_words([{'text':'thiếu mốc'}],1)


def test_timed_captions_restore_punctuation_and_keep_short_readable_phrases():
    from studio.speech import timed_captions
    text = 'Một: kiểm tra môi trường. Hai: sao lưu, giữ bản gốc.'
    clean = 'Một kiểm tra môi trường Hai sao lưu giữ bản gốc'.split()
    words = [{'text': w, 'start': i * .4, 'duration': .25} for i, w in enumerate(clean)]
    cues = timed_captions(words, 1., 1., text=text, max_chars=24)
    assert ' '.join(' '.join(c['text'].split()) for c in cues) == text
    assert all(len(line) <= 24 for c in cues for line in c['text'].splitlines())
    assert all(c['end'] - c['start'] <= 4.1 for c in cues)
    assert cues[0]['text'].endswith('trường.')
    assert cues[0]['start'] == 1.
    assert all(a['end'] < b['start'] for a, b in pairwise(cues))
    # A changed transcript must never be silently attached to unrelated audio words.
    fallback = timed_captions(words, 0., 1., text='Lời khác hoàn toàn.', max_chars=24)
    assert 'Một' in fallback[0]['text'] and 'khác' not in str(fallback)
    long=timed_captions([{'text':'x'*170,'start':0.,'duration':2.}],0.,1.,max_chars=24)
    assert ''.join(c['text'].replace('\n','') for c in long)=='x'*170
    with pytest.raises(ValueError):timed_captions(words,0.,1.,max_chars=0)


def test_renderer_honors_caption_width_and_original_narration_punctuation(tmp_path, monkeypatch):
    from studio.bus import InMemoryBus
    from studio.events import Scene, SceneManifest
    from studio.renderer import Renderer
    cfg=MediaConfig(output_dir=tmp_path,video={'provider':'fake','caption_max_chars':18})
    suite=make_media(cfg)
    text='Một: kiểm tra. Hai: sao lưu bản gốc.'
    def generate(text,voice,path):
        path.write_bytes(b'audio fixture')
        tokens='Một kiểm tra Hai sao lưu bản gốc'.split()
        return MediaResult(path,'test','timed',4.,words=[{'text':w,'start':i*.4,'duration':.25} for i,w in enumerate(tokens)],timing_source='provider')
    monkeypatch.setattr(suite.tts,'synthesize',generate)
    renderer=Renderer(InMemoryBus(),suite)
    manifest=SceneManifest(video_id='V1',scenes=[Scene(scene_id='S1',order=0,narration=text,visual_prompt='Hình')])
    renderer.render(manifest)
    cues=renderer.caption_cues(manifest)
    assert ' '.join(' '.join(c.text.split()) for c in cues)==text
    assert all(len(line)<=18 for c in cues for line in c.text.splitlines())


def test_duration_boundary_keeps_sentence_ending_with_its_preceding_words():
    from studio.speech import timed_captions
    text='Tắt tiếng, xem phụ đề: kịp đọc, đúng chính tả, khớp lời không?'
    tokens=text.split()
    starts=[.1375,.3,.8625,1.1625,1.375,1.9125,2.1,2.5875,2.8375,3.0375,3.6,3.8,4.0375]
    spans=[.1625,.3875,.3,.2125,.35,.1875,.25,.25,.2,.4,.2,.2375,.2875]
    words=[{'text':word.strip('.,:;!?'),'start':start,'duration':span}
           for word,start,span in zip(tokens,starts,spans,strict=True)]
    cues=timed_captions(words,0.,1.,text=text,max_chars=38)
    assert ' '.join(' '.join(c['text'].split()) for c in cues)==text
    assert all(len(c['text'].split())>1 for c in cues)
    assert all(c['end']-c['start']<=4.12 for c in cues)


def test_caption_punctuation_survives_unspoken_pause_markers_from_editor():
    from studio.speech import timed_captions
    tokens='Bật tiếng vừa nghe vừa đọc phụ đề khớp lời đúng chính tả đúng lúc chưa'.split()
    words=[{'text':word,'start':i*.35,'duration':.2} for i,word in enumerate(tokens)]
    text='Bật tiếng, … vừa nghe vừa đọc phụ đề: … khớp lời, … đúng chính tả, … đúng lúc chưa?'
    cues=timed_captions(words,0,1,text=text,max_chars=38)
    assert ' '.join(' '.join(c['text'].split()) for c in cues)=='Bật tiếng, vừa nghe vừa đọc phụ đề: khớp lời, đúng chính tả, đúng lúc chưa?'


def test_portrait_wrapping_does_not_create_a_one_word_flash_between_measured_words():
    from studio.speech import timed_captions
    text='Quy tắc dễ nhớ là giữ nguồn riêng, chỉnh trên bản làm việc.'
    starts=[0.,.28,.44,.62,.84,1.02,1.22,1.48,1.9,2.14,2.24,2.48,2.66]
    spans=[.28,.16,.18,.22,.18,.2,.26,.26,.24,.1,.24,.18,.18]
    words=[{'text':word,'start':start,'duration':span}
           for word,start,span in zip(text.split(),starts,spans,strict=True)]
    cues=timed_captions(words,0.,1.,max_chars=26)
    assert all(len(c['text'].split())>1 for c in cues)
    assert all(c['end']-c['start']>=.3 for c in cues)
    assert ' '.join(' '.join(c['text'].split()) for c in cues)==text
    assert all(len(line)<=26 for c in cues for line in c['text'].splitlines())
