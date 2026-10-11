"""Capture public pages in a fresh browser. Every network fetch uses the existing pinned-IP transport."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .tools import MAX_BYTES, default_fetcher, pin_url

# No arbitrary actions supplied by a model. Navigation and scrolling are the only browser actions.
EVIDENCE_JS = """() => ({url:location.href,title:document.title,
 text:document.body.innerText.slice(0,10000),
 links:[...document.querySelectorAll('a[href]')].map(a=>({url:a.href,text:a.innerText.trim().slice(0,100)}))
 .filter(a=>a.text).slice(0,120)})"""


class ReadOnlyNetwork:
    def __init__(self, checkpoint: Any = lambda: None, pinned_hosts: dict[str, str] | None = None):
        self.pinned_hosts = pinned_hosts or {}
        self.checkpoint = checkpoint; self.deadline = time.monotonic() + 180
        self.requests = 0; self.bytes = 0; self.blocked = 0
        self.cache: dict[str, tuple[int, str, str, bytes]] = {}
        self.semaphore = asyncio.Semaphore(6)

    async def route(self, route: Any) -> None:
        self.checkpoint()
        request = route.request; self.requests += 1
        if request.method != 'GET' or self.requests > 1024 or self.bytes > 128 * 1024 * 1024 or time.monotonic() > self.deadline:
            self.blocked += 1; await route.abort(); return
        u = urlsplit(request.url)
        if u.hostname in self.pinned_hosts and u.scheme in {'http', 'https'} and u.port in {None, 80, 443}:
            await route.continue_(); return
        try:
            headers = {k: v for k, v in request.headers.items() if k in
                       {'accept', 'rsc', 'next-router-prefetch', 'next-router-state-tree', 'next-url'}}
            cache_key = request.url + json.dumps(headers, sort_keys=True)
            result = self.cache.get(cache_key)
            if result is None:
                async with self.semaphore:
                    self.checkpoint()
                    result = await asyncio.to_thread(default_fetcher, request.url, request_headers=headers)
                if len(result[3]) > MAX_BYTES: raise ValueError('Resource exceeds capture limit')
                self.bytes += len(result[3]); self.cache[cache_key] = result
            status, content_type, _, body = result
            await route.fulfill(status=status, body=body, headers={'Content-Type': content_type,
                                'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store'})
        except Exception:
            self.blocked += 1; await route.abort()


    def observe_response(self, response: Any) -> None:
        if urlsplit(response.url).hostname not in self.pinned_hosts: return
        try: size = int(response.headers.get('content-length', '0'))
        except ValueError: size = 0
        self.bytes += max(0, size)
        if size > MAX_BYTES: self.bytes = 128 * 1024 * 1024 + 1


def check_capture(path: Path) -> None:
    from PIL import Image, ImageStat
    with Image.open(path) as image:
        if image.size != (3200, 2000): raise ValueError('Ảnh nguồn phải là bản chụp 3200 × 2000 thật')
        if max(ImageStat.Stat(image.convert('RGB').resize((160, 100))).stddev) < 2:
            raise ValueError('Ảnh chụp trống; website chưa hiển thị nội dung')


def discover_links(pages: list[dict], host: str, visited: set[str]) -> list[str]:
    result = []
    for page in pages:
        for link in page['links']:
            url = link['url'].split('#')[0]; u = urlsplit(url)
            if (u.scheme not in {'http', 'https'} or u.hostname != host or u.query or url in visited or url in result
                    or re.search(r'logout|signout|delete|remove|checkout|payment|unsubscribe', u.path, re.I)):
                continue
            result.append(url)
    return result


def capture_site(url: str, directory: Path, limit: int, checkpoint: Any) -> list[dict]:
    return asyncio.run(_capture_site(url, directory, limit, checkpoint))


async def _capture_site(url: str, directory: Path, limit: int, checkpoint: Any) -> list[dict]:
    from playwright.async_api import async_playwright
    url, _ = pin_url(url)
    pages: list[dict] = []; visited: set[str] = set()
    # Resolve redirects through the pinned transport before browsing: evidence keeps the real canonical URL.
    initial = await asyncio.to_thread(default_fetcher, url)
    if initial[0] >= 400 or len(initial[3]) > MAX_BYTES: raise ValueError('Website không trả nội dung công khai hợp lệ')
    url, ip = pin_url(initial[2]); host = urlsplit(url).hostname or ''
    network = ReadOnlyNetwork(checkpoint, pinned_hosts={host: ip}); candidates = [url]
    directory.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        executable = os.environ.get('STUDIO_WEBSITE_CHROMIUM')
        browser = await playwright.chromium.launch(headless=True, executable_path=executable,
                    args=[f'--host-resolver-rules=MAP {host} {ip}, MAP * ~NOTFOUND', '--disable-quic', '--disable-background-networking',
                          '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
        try:
            context = await browser.new_context(viewport={'width': 1600, 'height': 1000}, device_scale_factor=2,
                                                 service_workers='block', accept_downloads=False, locale='vi-VN', reduced_motion='reduce')
            context.on('response', network.observe_response)
            await context.route('**/*', network.route)
            await context.route_web_socket('**/*', lambda socket: socket.close())
            page = await context.new_page()
            page.set_default_timeout(15000)
            attempts = 0
            while candidates and len(pages) < limit and attempts < limit * 2:
                attempts += 1
                checkpoint(); target = candidates.pop(0); visited.add(target)
                try:
                    response = await page.goto(target, wait_until='domcontentloaded', timeout=45000)
                    if response is None or response.status >= 400: raise ValueError('Trang trả lỗi HTTP')
                    await page.wait_for_function("document.body && (document.querySelector('main') || document.body).innerText.trim().length > 100", timeout=30000)
                    await page.wait_for_timeout(2000)
                    await page.wait_for_function('document.body.innerText.trim().length > 200', timeout=15000)
                    data = await page.evaluate(EVIDENCE_JS)
                    if len(data['text'].strip()) < 200: raise ValueError('Trang chưa hiển thị nội dung ổn định')
                    if network.bytes > 128 * 1024 * 1024: raise ValueError('Website vượt giới hạn tài nguyên chụp')
                    if re.search(r'checking your browser|verify you are human|access denied', data['text'][:600], re.I):
                        raise ValueError('Website yêu cầu xác minh truy cập')
                    if re.search(r'/(login|signin|sign-in|auth)(/|$)', urlsplit(data['url']).path, re.I) and data['url'] != target:
                        raise ValueError('Trang này chuyển đến màn hình đăng nhập')
                    data.update(id=len(pages), image=f'page-{len(pages)}.png', capture_version=4)
                    await page.screenshot(path=str(directory / data['image']), animations='allow')
                    check_capture(directory / data['image'])
                    if any(p['url'] == data['url'] and p['text'] == data['text'] for p in pages):
                        continue
                    pages.append(data)
                    host = urlsplit(data['url']).hostname or ''
                    candidates = discover_links(pages, host, visited)
                except Exception as exc:
                    from .website import ExportInterrupted
                    if isinstance(exc, ExportInterrupted) or not pages: raise
            if not pages: raise ValueError('Không chụp được nội dung công khai')
            if len(pages) == 1:
                await page.goto(pages[0]['url'], wait_until='domcontentloaded', timeout=45000)
                for y in (700, 1400):
                    checkpoint(); await page.evaluate('(y) => window.scrollTo(0,y)', y); await page.wait_for_timeout(250)
                    await page.wait_for_function('document.body.innerText.trim().length > 200', timeout=15000)
                    data = await page.evaluate(EVIDENCE_JS)
                    if len(data['text'].strip()) < 200: raise ValueError('Trang chưa hiển thị nội dung ổn định')
                    if network.bytes > 128 * 1024 * 1024: raise ValueError('Website vượt giới hạn tài nguyên chụp')
                    data.update(id=len(pages), image=f'page-{len(pages)}.png', scroll_y=y, capture_version=4)
                    await page.screenshot(path=str(directory / data['image']), animations='allow')
                    check_capture(directory / data['image']); pages.append(data)
        finally: await browser.close()
    return pages
