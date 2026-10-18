import hashlib
import json
import runpy
import sys
from contextlib import nullcontext
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest


def wrapper():
    return runpy.run_path(str(Path(__file__).parents[1] / 'scripts/vieneu-speech.py'))


def test_vieneu_word_timing_retains_punctuation_and_measured_offsets():
    words = wrapper()['verified_words']('Hình cần rõ.', [
        {'text': 'Hình', 'start': .12, 'end': .4},
        {'text': 'cần', 'start': .4, 'end': .7},
        {'text': 'rõ', 'start': .7, 'end': 1.1}], 1.3)
    assert [w['text'] for w in words] == ['Hình', 'cần', 'rõ.']
    assert words[-1]['start'] == .7
    assert words[-1]['duration'] == pytest.approx(.4)


@pytest.mark.parametrize('recognized', ['xõ', ''])
def test_vieneu_refuses_unverified_transcript_instead_of_relabeling_words(recognized):
    with pytest.raises(ValueError, match='transcript'):
        wrapper()['verified_words']('rõ', [{'text': recognized, 'start': 0., 'end': .4}], .5)


def test_vieneu_rejects_zero_length_word_timing():
    with pytest.raises(ValueError, match='timing'):
        wrapper()['verified_words']('rõ', [{'text': 'rõ', 'start': .2, 'end': .2}], .5)


def test_vieneu_detects_mutated_preset_assets_before_synthesis(tmp_path):
    voice = tmp_path / 'voices.json'
    voice.write_bytes(b'authorized preset')
    manifest = tmp_path / 'artifacts.json'
    manifest.write_text(json.dumps({'files': [
        {'path': str(voice), 'sha256': hashlib.sha256(voice.read_bytes()).hexdigest()}]}))
    wrapper()['verify_artifacts'](manifest)
    voice.write_bytes(b'changed preset')
    with pytest.raises(ValueError, match='checksum'):
        wrapper()['verify_artifacts'](manifest)


@pytest.mark.parametrize('passing_seed,expected_seeds,expected_models',[
    (23,[23],1),(24,[23,24],3),(None,[23,24,25],6),
])
def test_synthesis_retries_distinct_seeds_only_until_real_transcript_matches(
        tmp_path,monkeypatch,passing_seed,expected_seeds,expected_models):
    seeds=[];models=[]
    class Samples:
        ndim=1
        def __len__(self):return 16000
        def astype(self,_):return self
    class Voice:
        def __init__(self,**kwargs):pass
        def list_preset_voices(self):return [('Voice','Ngọc Huyền')]
        def infer(self,text,**kwargs):return seeds[-1]
        def save(self,audio,path):Path(path).write_bytes(f'candidate-{audio}'.encode())
    class Recognizer:
        def __init__(self,name,**kwargs):models.append((name,seeds[-1]))
        def transcribe(self,samples,**kwargs):
            assert 'initial_prompt' not in kwargs
            spoken='chào.' if seeds[-1]==passing_seed else 'chảo.'
            words=[SimpleNamespace(word='Xin',start=.1,end=.35),SimpleNamespace(word=spoken,start=.35,end=.75)]
            return [SimpleNamespace(words=words)],None
    monkeypatch.setitem(sys.modules,'numpy',SimpleNamespace(random=SimpleNamespace(seed=seeds.append),float32='float32'))
    monkeypatch.setitem(sys.modules,'soundfile',SimpleNamespace(read=lambda *a,**kw:(Samples(),16000)))
    monkeypatch.setitem(sys.modules,'soxr',SimpleNamespace(resample=lambda samples,*a:samples))
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=Recognizer))
    monkeypatch.setitem(sys.modules,'vieneu',SimpleNamespace(Vieneu=Voice))
    manifest=tmp_path/'artifacts.json';out=tmp_path/'voice.wav'
    manifest.write_text(json.dumps({'files':[],'hf_home':str(tmp_path),'backbone':str(tmp_path),'model_revision':'pinned'}))
    monkeypatch.setattr(sys,'argv',['vieneu-speech','--artifacts',str(manifest),'--asr-cache',str(tmp_path),'--out',str(out)])
    monkeypatch.setattr(sys,'stdin',StringIO('Xin chào.'))
    for name in ['HF_HOME','HF_HUB_OFFLINE','HF_HUB_DISABLE_XET']:monkeypatch.setenv(name,'0')
    with pytest.raises(ValueError,match='ASR') if passing_seed is None else nullcontext():
        wrapper()['main']()
    assert seeds==expected_seeds
    assert len(models)==expected_models
    recognition=json.loads(out.with_suffix('.recognition.json').read_text(encoding='utf-8'))
    assert recognition['initial_seed']==23
    assert {a['seed'] for a in recognition['attempts']}==set(expected_seeds)
    assert recognition['no_initial_prompt']
    if passing_seed is None:
        assert not out.with_suffix('.timing.json').exists()
    else:
        timing=json.loads(out.with_suffix('.timing.json').read_text(encoding='utf-8'))
        assert timing['sha256']==hashlib.sha256(out.read_bytes()).hexdigest()
        assert f'seed={passing_seed}' in timing['source']
