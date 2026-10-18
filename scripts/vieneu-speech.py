"""Licensed VieNeu preset speech with offline, transcript-checked word timing.

Run in a pinned vieneu/faster-whisper environment. No voice cloning or upload.
The artifact manifest binds the model, codec and bundled voices to their hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path


def verify_artifacts(manifest: Path) -> dict:
    record = json.loads(manifest.read_text(encoding='utf-8'))
    for artifact in record['files']:
        path = Path(artifact['path'])
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != artifact['sha256']:
            raise ValueError(f'Voice/model artifact checksum changed: {path.name}')
    return record


def verified_words(text: str, recognized: list[dict], duration: float) -> list[dict]:
    tokens = text.split()
    def normalize(value: str) -> str:
        return ''.join(re.findall(r'\w+', value.casefold()))
    if [normalize(token) for token in tokens] != [normalize(word['text']) for word in recognized]:
        raise ValueError('Speech recognition transcript differs from narration; review audio before export')
    words = []
    previous = -1.
    for token, word in zip(tokens, recognized, strict=True):
        start, end = float(word['start']), float(word['end'])
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration + .02 and start >= previous):
            raise ValueError('Invalid measured word timing')
        words.append({'text': token, 'start': start, 'duration': end-start})
        previous = start
    return words


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--asr-cache', type=Path, required=True)
    parser.add_argument('--asr-model', default='auto')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--voice', default='Ngọc Huyền')
    parser.add_argument('--seed', type=int, default=23)
    parser.add_argument('--synthesis-attempts', type=int, choices=range(1,4), default=3)
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()
    text = sys.stdin.read().strip()
    if not text:
        raise ValueError('Empty narration')
    artifacts = verify_artifacts(args.artifacts)
    os.environ['HF_HOME'] = artifacts['hf_home']
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    import numpy as np
    import soundfile as sf
    import soxr
    from faster_whisper import WhisperModel
    from vieneu import Vieneu
    tts = Vieneu(backbone_repo=artifacts['backbone'],
                 onnx_dir=str(Path(artifacts['backbone'])/'onnx_update'),
                 backend='onnx', device='cpu', threads=args.threads)
    if args.voice not in {voice for _, voice in tts.list_preset_voices()}:
        raise ValueError('Voice must be a bundled preset covered by the pinned release')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix('.timing.json').unlink(missing_ok=True)
    attempts = []
    models = ['small', 'large-v3-turbo'] if args.asr_model == 'auto' else [args.asr_model]
    words = None
    for candidate in range(args.synthesis_attempts):
        seed = args.seed + candidate
        np.random.seed(seed)
        audio = tts.infer(text, voice=args.voice, temperature=.8)
        tts.save(audio, str(args.out))
        audio_sha = hashlib.sha256(args.out.read_bytes()).hexdigest()
        saved_audio, sample_rate = sf.read(str(args.out), dtype='float32')
        duration = len(saved_audio)/sample_rate
        mono = saved_audio.mean(axis=1) if saved_audio.ndim == 2 else saved_audio
        samples = soxr.resample(mono, sample_rate, 16000).astype(np.float32)
        for model_name in models:
            recognizer = WhisperModel(model_name, device='cpu', compute_type='int8',
                cpu_threads=args.threads, download_root=str(args.asr_cache), local_files_only=True)
            segments, _ = recognizer.transcribe(samples, language='vi', beam_size=3,
                vad_filter=True, condition_on_previous_text=False, word_timestamps=True)
            recognized = [{'text':w.word.strip(), 'start':w.start, 'end':w.end}
                          for segment in segments for w in segment.words]
            attempt = {'model':model_name, 'seed':seed, 'audio_sha256':audio_sha, 'recognized_words':recognized}
            attempts.append(attempt)
            try:
                words = verified_words(text, recognized, duration)
                attempt['exact_transcript_and_valid_timing'] = True
            except ValueError as error:
                attempt.update(exact_transcript_and_valid_timing=False, error=str(error))
            del recognizer
            if words is not None:
                break
        recognition = {'expected':text, 'attempts':attempts, 'voice':args.voice,
                       'no_initial_prompt':True, 'initial_seed':args.seed, 'seed':seed,
                       'synthesis_attempts':candidate+1, 'model_revision':artifacts['model_revision']}
        args.out.with_suffix('.recognition.json').write_text(
            json.dumps(recognition, ensure_ascii=False, indent=2), encoding='utf-8')
        if words is not None:
            break
    if words is None:
        raise ValueError('No ASR attempt verified the full transcript and measured timing')
    args.out.with_suffix('.timing.json').write_text(json.dumps({
        'sha256':hashlib.sha256(args.out.read_bytes()).hexdigest(),
        'source':f'faster-whisper-{attempts[-1]["model"]}-transcript-verified-word-boundaries; seed={seed}; candidates={candidate+1}',
        'words':words}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
