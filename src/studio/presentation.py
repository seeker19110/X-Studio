"""Native editorial frames and subtitle geometry shared by normal renders and portrait excerpts."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .media import MediaResult, find_font
from .render_cache import digest
from .timeline import Cue, stamp

REVISION = 1
QUALITY_DIRECTION = [
    'Lời đọc dùng câu ngắn, tự nhiên; tránh câu đệm lặp và giữ đủ hook/CTA trong lời đọc thực tế.',
    'Mỗi cảnh một ý, thường 5–12 giây; heading ngắn có nghĩa, khớp cảnh thay vì mã cảnh. Không giữ một hình quá 20 giây.',
    'Tiêu đề và chữ hiển thị chỉ hứa nội dung thực sự được đọc; quyền dùng tư liệu và duyệt đăng vẫn phải kiểm riêng.',
    'Không dùng thời lượng video để hứa tốc độ thao tác. Tiêu đề, mô tả và mọi tiêu đề A/B không được hứa hoàn thành công việc trong số giây/phút chưa đo.',
    'Thumbnail: overlay tối đa bốn từ, không dấu chấm câu, không lặp nguyên cụm trong tiêu đề. Ghi chú style phải dẫn đúng lời đọc hiện tại, không trích bản cũ.',
]


def font_evidence(font: Path | None) -> dict:
    if font is None or not font.is_file():return {'license':'unknown','path':str(font),'sha256':''}
    sha=digest(font)
    registry=Path(__file__).resolve().parents[2]/'docs/rights/font-evidence.json'
    records=json.loads(registry.read_text(encoding='utf-8'))['fonts']
    record=next((item for item in records if item['sha256']==sha),{'license':'unknown'})
    return {**record,'path':str(font),'sha256':sha}


def caption_width(video: dict, aspect: str) -> int:
    return int(video.get('caption_max_chars_portrait',26) if aspect=='9:16' else video.get('caption_max_chars',38))


def compose_frame(source: Path, out: Path, resolution: str, title: str, heading: str,
                  index: int, total: int, font: Path | None = None) -> MediaResult:
    font = font or find_font()
    if font is None: raise ValueError('Editorial layout requires a Unicode font')
    width,height=map(int,resolution.split('x')); portrait=height>width
    sx=width/(1080 if portrait else 1920); sy=height/(1920 if portrait else 1080)
    image=Image.new('RGB',(width,height),'#f3f1e9');draw=ImageDraw.Draw(image)
    def rect(box: tuple[int,int,int,int], color: str, radius: int = 24) -> tuple[int,int,int,int]:
        scaled=[int(v*(sx if i%2==0 else sy)) for i,v in enumerate(box)]
        coords=(scaled[0],scaled[1],scaled[2],scaled[3])
        draw.rounded_rectangle(coords,radius=max(1,int(radius*min(sx,sy))),fill=color)
        return coords
    def text(value: str, box: tuple[int,int,int,int], size: int, color: str = '#132c38') -> None:
        x,y,right,bottom=[int(v*(sx if i%2==0 else sy)) for i,v in enumerate(box)]
        size=max(1,int(size*min(sx,sy)))
        while True:
            face=ImageFont.truetype(str(font),size)
            lines=textwrap.wrap(value,width=max(1,int((right-x)/max(1,face.getlength('M')*.72))),break_on_hyphens=False) or ['']
            if size==1 or (max(face.getlength(line) for line in lines)<=right-x and len(lines)*size*1.3<=bottom-y): break
            size-=1
        draw.multiline_text((x,y),'\n'.join(lines),font=face,fill=color,spacing=max(1,int(size*.18)))
    if portrait:
        text(title,(90,125,940,245),27,'#4d6762')
        text(f'{index+1:02d} / {total:02d}',(90,295,940,350),28,'#34786c')
        text(heading,(90,375,940,660),80)
        art=rect((90,745,940,1310),'#e3ece2')
        rect((90,1470,940,1660),'#132c38')
        rail=(90,1330,940,1435)
    else:
        text(title,(110,65,1810,160),27,'#4d6762')
        text(f'{index+1:02d} / {total:02d}',(110,225,880,280),28,'#34786c')
        text(heading,(110,315,890,650),95)
        art=rect((970,180,1810,700),'#e3ece2')
        rect((100,825,1820,1008),'#132c38')
        rail=(110,735,1810,793)
    padding=max(1,int(22*min(sx,sy)))
    with Image.open(source) as raw:
        fitted=ImageOps.contain(raw.convert('RGB'),(max(1,art[2]-art[0]-2*padding),max(1,art[3]-art[1]-2*padding)),Image.Resampling.LANCZOS)
        image.paste(fitted,((art[0]+art[2]-fitted.width)//2,(art[1]+art[3]-fitted.height)//2))
    count=min(total,12); step=(rail[2]-rail[0])/max(count,1)
    for i in range(count):
        x=int(rail[0]+i*step); right=int(rail[0]+(i+1)*step-12)
        rect((x,rail[1],right,rail[3]),'#163e39' if i==index else '#dfe6dc',12)
        text(str(i+1),(x+20,rail[1]+8,right,rail[3]-4),26,'#ffffff' if i==index else '#34786c')
    out.parent.mkdir(parents=True,exist_ok=True); image.save(out)
    return MediaResult(out,'studio','editorial-layout-v1')


def write_captions(cues: list[Cue], resolution: str, out: Path, font: Path | None = None) -> None:
    font=font or find_font()
    if font is None: raise ValueError('Readable captions require a Unicode font')
    width,height=map(int,resolution.split('x')); portrait=height>width
    scale=width/(1080 if portrait else 1920)
    family=(ImageFont.truetype(str(font),12).getname()[0] or 'DejaVu Sans').replace(',',' ')
    size=max(1,round(52*scale)); margin_v=round(height*(310/1920 if portrait else 126/1080))
    left=round(width*(90/1080 if portrait else 100/1920)); right=round(width*(140/1080 if portrait else 100/1920))
    body=(f'[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\n'
        '[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n'
        f'Style: Default,{family},{size},&H00FFFFFF,&H00FFFFFF,&H00382C13,&H00382C13,0,0,0,0,100,100,0,0,1,0,0,2,{left},{right},{margin_v},1\n'
        '[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n')
    for cue in cues:
        start=stamp(cue.start,'.')[:-1];end=stamp(cue.end,'.')[:-1]
        value=cue.text.replace('\\','\\\\').replace('{',r'\{').replace('}',r'\}').replace('\n',r'\N')
        body+=f'Dialogue: 0,{start},{end},Default,,0,0,0,,{value}\n'
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(body,encoding='utf-8')
