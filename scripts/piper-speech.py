"""Local Piper command adapter: UTF-8 stdin -> WAV + checksum-bound timing.

Run in an isolated environment with piper-tts[alignment]. Model stays unchanged;
Piper exposes duration tensors in memory. No network or account is used here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import wave
from pathlib import Path


def boundaries(text, chunk, offset):
    rate=chunk.sample_rate; groups=[]; position=0; start=None
    for alignment in chunk.phoneme_alignments or []:
        if alignment.phoneme in {' ', '$'}:
            if start is not None and position>start:groups.append((start,position));start=None
        elif alignment.phoneme not in {'^','_'} and start is None:start=position
        position+=alignment.num_samples
    if start is not None and position>start:groups.append((start,position))
    tokens=text.split()
    if len(groups)==len(tokens):
        return ([{'text':token,'start':offset+left/rate,'duration':(right-left)/rate}
                 for token,(left,right) in zip(tokens,groups,strict=True)],'piper-phoneme-alignment')
    return ([{'text':text,'start':offset,'duration':len(chunk.audio_int16_bytes)/2/rate}],
            'piper-sentence-boundary')


def main():
    from piper import PiperVoice, SynthesisConfig
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--length-scale',type=float,default=1.35)
    parser.add_argument('--noise-scale',type=float)
    parser.add_argument('--noise-w-scale',type=float)
    parser.add_argument('--sentence-silence',type=float,default=0.)
    args=parser.parse_args();text=sys.stdin.read().strip()
    if not text:raise ValueError('Empty narration')
    voice=PiperVoice.load(args.model,include_alignments=True)
    if not 0 <= args.sentence_silence <= 2:raise ValueError('Sentence silence must be between 0 and 2 seconds')
    config=SynthesisConfig(length_scale=args.length_scale,noise_scale=args.noise_scale,noise_w_scale=args.noise_w_scale)
    words=[];sources=set();offset=0.
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(args.out),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(voice.config.sample_rate)
        sentences=list(re.finditer(r'[^.!?]+(?:[.!?]+|$)',text))
        for index,match in enumerate(sentences):
            sentence=match.group().strip()
            chunks=list(voice.synthesize(sentence,syn_config=config,include_alignments=True))
            if len(chunks)==1:
                aligned,source=boundaries(sentence,chunks[0],offset);words.extend(aligned);sources.add(source)
            else:
                duration=sum(len(c.audio_int16_bytes)/2/c.sample_rate for c in chunks)
                words.append({'text':sentence,'start':offset,'duration':duration});sources.add('piper-sentence-boundary')
            for chunk in chunks:
                stream.writeframes(chunk.audio_int16_bytes);offset+=len(chunk.audio_int16_bytes)/2/chunk.sample_rate
            if index+1<len(sentences):
                samples=round(args.sentence_silence*voice.config.sample_rate)
                stream.writeframes(b'\0\0'*samples);offset+=samples/voice.config.sample_rate
    source='piper-phoneme-alignment' if sources=={'piper-phoneme-alignment'} else 'piper-sentence-boundary'
    args.out.with_suffix('.timing.json').write_text(json.dumps({'sha256':hashlib.sha256(args.out.read_bytes()).hexdigest(),
        'source':source,'words':words},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
