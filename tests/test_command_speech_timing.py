import hashlib
import json
from types import SimpleNamespace

import pytest

from studio.media import CommandTTS, MediaConfig, MediaError


@pytest.mark.parametrize('fault',[None,'missing','hash','words','transcript','source','json'])
def test_command_timing_is_opt_in_and_bound_to_audio_and_transcript(tmp_path,monkeypatch,fault):
    path=tmp_path/'speech.wav'
    def run(spec):
        path.write_bytes(b'audio')
        record={'sha256':hashlib.sha256(b'audio').hexdigest(),'source':'piper-phoneme-alignment',
                'words':[{'text':'Xin chào.','start':0.,'duration':1.}]}
        if fault=='hash':record['sha256']='bad'
        if fault=='words':record['words'][0]['start']=-1
        if fault=='transcript':record['words'][0]['text']='Khác chữ.'
        if fault=='source':record['source']=''
        if fault!='missing':path.with_suffix('.timing.json').write_text('{' if fault=='json' else json.dumps(record))
        return SimpleNamespace(timed_out=False,exit_code=0)
    monkeypatch.setattr('studio.media.audio_duration',lambda *a:1.2)
    adapter=CommandTTS(MediaConfig(tts={'provider':'command','command':'fixture {out}','timing_sidecar':True}),
                       sandbox=SimpleNamespace(run=run))
    if fault:
        with pytest.raises(MediaError,match='timing'):adapter.synthesize('Xin chào.',{},path)
    else:
        result=adapter.synthesize('Xin chào.',{},path)
        assert result.words[0]['text']=='Xin chào.' and result.timing_source=='piper-phoneme-alignment'


@pytest.mark.parametrize('aligned',[True,False])
def test_piper_wrapper_uses_phoneme_samples_or_labels_sentence_fallback(aligned):
    import runpy
    from pathlib import Path
    module=runpy.run_path(str(Path(__file__).parents[1]/'scripts/piper-speech.py'))
    phonemes=[('^',2),('x',10),(' ',2),('c',12),('$',2)]
    chunk=SimpleNamespace(sample_rate=10,audio_int16_bytes=b'0'*56,
        phoneme_alignments=[SimpleNamespace(phoneme=p,num_samples=n) for p,n in phonemes] if aligned else None)
    words,source=module['boundaries']('Xin chào.',chunk,1)
    assert source==('piper-phoneme-alignment' if aligned else 'piper-sentence-boundary')
    assert ' '.join(w['text'] for w in words)=='Xin chào.'
    assert words[0]['start']==(1.2 if aligned else 1)


def test_piper_sentence_pause_preserves_measured_word_offsets(tmp_path,monkeypatch):
    import io
    import runpy
    import sys
    import wave
    from pathlib import Path
    calls=[]
    chunk=SimpleNamespace(sample_rate=100,audio_int16_bytes=b'\0\0'*100,
        phoneme_alignments=[SimpleNamespace(phoneme=p,num_samples=n)
                            for p,n in [('^',4),('x',40),(' ',4),('c',48),('$',4)]])
    def synthesize(text,syn_config,include_alignments):
        calls.append((text,syn_config));return [chunk]
    voice=SimpleNamespace(config=SimpleNamespace(sample_rate=100),synthesize=synthesize)
    monkeypatch.setitem(sys.modules,'piper',SimpleNamespace(PiperVoice=SimpleNamespace(load=lambda *a,**k:voice),
                                                          SynthesisConfig=SimpleNamespace))
    target=tmp_path/'speech.wav'
    monkeypatch.setattr(sys,'argv',['piper-speech','--model','fixture.onnx','--out',str(target),
                                  '--noise-scale','.333','--noise-w-scale','.2','--sentence-silence','.18'])
    monkeypatch.setattr(sys,'stdin',io.StringIO('Xin chào. Rà lại.'))
    runpy.run_path(str(Path(__file__).parents[1]/'scripts/piper-speech.py'))['main']()
    with wave.open(str(target)) as stream:assert stream.getnframes()==218
    timing=json.loads(target.with_suffix('.timing.json').read_text(encoding='utf-8'))
    assert timing['words'][2]['start']==pytest.approx(1.22)
    assert calls[0][1].noise_scale==.333 and calls[0][1].noise_w_scale==.2
    assert ' '.join(w['text'] for w in timing['words'])=='Xin chào. Rà lại.'


@pytest.mark.parametrize('enricher',['_with_script','_with_provenance'])
def test_model_context_distinguishes_dataset_license_from_speech_release(tmp_path,enricher):
    from studio import orchestrator
    from studio.events import Envelope
    from studio.factory import Factory
    assessment={'publication_decision':'NOT_CLEARED','reason':'Lessac research lineage; dataset CC BY is not an output grant'}
    factory=Factory(tmp_path/'db',MediaConfig(output_dir=tmp_path/'out',tts={'rights_assessment':assessment}),demo=True)
    try:
        event=Envelope(topic='media-assets',key='V',actor='renderer',payload={'video_id':'V'})
        context=getattr(orchestrator,enricher)(event,factory.orch)
        assert context['speech_rights']==assessment
    finally:factory.close()
