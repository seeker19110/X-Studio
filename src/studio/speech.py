"""Shared provider word boundaries and subtitle grouping for both production pipelines."""
from __future__ import annotations

import math
from itertools import pairwise
from pathlib import Path


def validate_words(words: list[dict], duration: float) -> list[dict]:
    if not isinstance(words, list) or not words or len(words) > 100000 or not math.isfinite(duration) or duration <= 0:
        raise ValueError('Mốc từ hoặc thời lượng không hợp lệ')
    result = []; previous = -1.
    for word in words:
        try:
            text = word['text']; start = float(word['start']); span = float(word['duration'])
        except (KeyError, TypeError, ValueError) as exc: raise ValueError('Mốc từ không hợp lệ') from exc
        if (not isinstance(text,str) or not text.strip() or not math.isfinite(start) or not math.isfinite(span)
                or start < 0 or span <= 0 or start < previous or start + span > duration + .25):
            raise ValueError('Mốc từ vượt thời lượng hoặc không tăng dần')
        result.append({'text':text,'start':start,'duration':span}); previous = start
    return result


def caption_chunks(text: str, max_chars: int = 42) -> list[str]:
    chunks: list[str] = []; lines: list[str] = []; line = ''
    for word in text.split():
        # Even a single unbroken URL cannot overflow the safe subtitle width.
        pieces = [word[i:i + max_chars] for i in range(0, len(word), max_chars)]
        for piece in pieces:
            candidate = (line + ' ' + piece).strip()
            if len(candidate) > max_chars:
                lines.append(line); line = piece
                if len(lines) == 2: chunks.append('\n'.join(lines)); lines = []
            else: line = candidate
    if line: lines.append(line)
    if lines: chunks.append('\n'.join(lines))
    balanced = []
    protected = {'bản gốc','thư mục','phụ đề','sao lưu','lời đọc'}
    for chunk in chunks:
        if '\n' not in chunk:
            balanced.append(chunk); continue
        tokens = chunk.split(); choices = []
        for i in range(1,len(tokens)):
            left,right = ' '.join(tokens[:i]),' '.join(tokens[i:])
            if max(len(left),len(right)) > max_chars: continue
            pair = tokens[i-1].strip('.,:;!?').lower()+' '+tokens[i].strip('.,:;!?').lower()
            score = abs(len(left)-len(right)) + (100 if min(i,len(tokens)-i)==1 else 0) + (40 if pair in protected else 0)
            choices.append((score,left+'\n'+right))
        balanced.append(min(choices)[1] if choices else chunk)
    return balanced


def punctuated_words(words: list[dict], text: str) -> list[dict]:
    tokens = [token for token in text.split() if any(c.isalnum() for c in token)]
    def normalized(value: str) -> str: return ''.join(c for c in value.casefold() if c.isalnum())
    if [normalized(t) for t in tokens] != [normalized(t) for w in words for t in w['text'].split()]: return words
    restored = []; cursor = 0
    for word in words:
        count = len(word['text'].split())
        restored.append({**word, 'text': ' '.join(tokens[cursor:cursor+count])}); cursor += count
    return restored


async def edge_audio(text: str, voice: str, path: Path, rate: str = '+0%') -> list[dict]:
    import edge_tts
    words = []
    communicate = edge_tts.Communicate(text, voice, rate=rate, boundary='WordBoundary', connect_timeout=15, receive_timeout=60)
    with path.open('wb') as audio:
        async for chunk in communicate.stream():
            if chunk['type'] == 'audio': audio.write(chunk['data'])
            elif chunk['type'] == 'WordBoundary':
                words.append({'text': chunk['text'], 'start': chunk['offset'] / 10000000,
                              'duration': chunk['duration'] / 10000000})
    if not words or not path.stat().st_size: raise ValueError('Dịch vụ giọng đọc không trả âm thanh và mốc phụ đề')
    return words


def timed_captions(words: list[dict], offset: float, pace: float, *, text: str | None = None,
                   max_chars: int = 42) -> list[dict]:
    """Keep measured word times and restore source punctuation only when every token agrees."""
    if max_chars < 1 or not math.isfinite(pace) or pace <= 0: raise ValueError('Invalid caption width or pace')
    if text: words = punctuated_words(words,text)
    cues = []; group: list[dict] = []
    def emit() -> None:
        start = offset + group[0]['start'] / pace
        end = offset + (group[-1]['start'] + group[-1]['duration']) / pace + .12
        chunks = caption_chunks(' '.join(w['text'] for w in group),max_chars)
        weight = sum(len(chunk) for chunk in chunks); elapsed = 0
        for chunk in chunks:
            cues.append({'start':start+(end-start)*elapsed/weight,
                         'end':start+(end-start)*(elapsed+len(chunk))/weight,'text':chunk})
            elapsed += len(chunk)
    for index,word in enumerate(words):
        candidate = ' '.join(w['text'] for w in [*group, word])
        if group and (len(candidate) > min(72,max_chars*2-1) or len(caption_chunks(candidate,max_chars))>1
                      or group[-1]['text'].endswith(('.', '!', '?'))
                      or (word['start']+word['duration']-group[0]['start'])/pace > 4):
            carry: list[dict] = []
            if (len(group)>=3 and not group[-1]['text'].endswith(('.', '!', '?'))
                    and (word['text'].endswith(('.', '!', '?')) or index==len(words)-1)):
                carry=group[-2:];group=group[:-2]
            emit(); group = carry
        group.append(word)
    if group: emit()
    for current, following in pairwise(cues): current['end'] = min(current['end'], following['start'] - .025)
    return cues

