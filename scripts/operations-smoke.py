"""Offline browser integration: independent clients, legacy import recovery, conflicts, backup and restore."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from studio.backup import restore_backup
from studio.console import ConsoleServer
from studio.factory import Factory
from studio.media import MediaConfig, load_media_config


def main() -> None:
    screenshots = Path(os.environ.get('STUDIO_OPERATIONS_SCREENSHOTS','output/operations-smoke')); screenshots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='xstudio-operations-') as temp:
        root = Path(temp); os.environ['STUDIO_POLICY_KEY_FILE'] = str(root / 'policy.key')
        factory = Factory(root / 'studio.sqlite', MediaConfig(output_dir=root / 'out', production={'automation':'production'}), demo=True)
        server = ConsoleServer(('127.0.0.1', 0), factory, 'operations-test')
        http = threading.Thread(target=server.serve_forever, daemon=True); http.start()
        worker = threading.Thread(target=factory.work, daemon=True); worker.start()
        try:
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(headless=True, executable_path=os.environ.get('STUDIO_WEBSITE_CHROMIUM'))
                errors = []
                def open_page(context):
                    page = context.new_page(); page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/#ideas')
                    page.locator('#login-form [name=token]').fill('operations-test')
                    page.locator('#login-form button').click()
                    page.locator('#connection').filter(has_text='Đã kết nối').wait_for()
                    return page
                first, second = browser.new_context(), browser.new_context()
                legacy = [{'id':'legacy-a', 'title':'Ý tưởng từ trình duyệt cũ'}]
                first.add_init_script("if (!localStorage.getItem('xstudio.ideas.v1')) localStorage.setItem('xstudio.ideas.v1', " + json.dumps(json.dumps(legacy, ensure_ascii=False)) + ')')
                a = open_page(first); a.locator('.idea-card').filter(has_text=legacy[0]['title']).wait_for()
                b = open_page(second); b.locator('.idea-card').filter(has_text=legacy[0]['title']).wait_for()
                assert b.evaluate("localStorage.getItem('xstudio.ideas.v1')") is None
                a.locator('[data-edit-idea]').first.click(); a.locator('#idea-form [name=title]').fill('Bản nháp tab A')
                b.locator('[data-edit-idea]').first.click(); b.locator('#idea-form [name=title]').fill('Đã sửa từ tab B')
                b.locator('#idea-form [type=submit]').click(); b.locator('#idea-dialog').wait_for(state='hidden')
                a.locator('#idea-form [type=submit]').click()
                a.locator('#idea-error').filter(has_text='tab khác').wait_for()
                assert a.locator('#idea-form [name=title]').input_value() == 'Bản nháp tab A'
                a.locator('#idea-dialog [data-close]').first.click()
                third = browser.new_context()
                raw = json.dumps([{'id':'retry-import','title':'Nhập lại sau lỗi mạng'}], ensure_ascii=False)
                third.add_init_script("localStorage.setItem('xstudio.ideas.v1', " + json.dumps(raw) + ')')
                def reject_import(route):
                    if route.request.method == 'POST' and route.request.post_data_json.get('action') == 'import':
                        route.fulfill(status=503, json={'error':'Thử lại sau'})
                    else: route.continue_()
                third.route('**/api/ideas', reject_import)
                c = open_page(third); c.locator('#idea-list .notice').filter(has_text='Chưa nhập').wait_for()
                assert c.evaluate("localStorage.getItem('xstudio.ideas.v1')") == raw
                assert not c.evaluate("Object.keys(localStorage).some(key => key.startsWith('xstudio.ideas.imported.'))")
                third.unroute('**/api/ideas'); c.reload()
                c.locator('.idea-card').filter(has_text='Nhập lại sau lỗi mạng').wait_for()
                assert c.evaluate("localStorage.getItem('xstudio.ideas.v1')") == raw
                from PIL import Image
                picture = root / 'picture.png'; Image.new('RGB',(320,180),'#00aa88').save(picture)
                c.locator('.nav[data-view=library]').click()
                c.locator('#library-upload input').set_input_files(str(picture))
                c.locator('#library-upload [type=submit]').click()
                c.locator('[data-library-edit]').first.wait_for()
                c.locator('[data-library-edit]').first.click()
                c.locator('#library-form [name=license]').select_option('owned')
                c.locator('#library-form [type=submit]').click()
                c.locator('.library-grid .badge').filter(has_text='owned').wait_for()
                factory.command('create',{'title':'Sửa cảnh qua giao diện','target_minutes':.2})
                import time
                deadline = time.monotonic()+45
                while time.monotonic()<deadline:
                    snapshot = factory.snapshot()
                    if snapshot['projects'] and any(g['kind']=='publish' for g in snapshot['gates']): break
                    time.sleep(.1)
                assert snapshot['projects'] and any(g['kind']=='publish' for g in snapshot['gates'])
                vid = snapshot['projects'][0]['id']; version = snapshot['projects'][0]['manifest_version']
                c.locator('.nav[data-view=projects]').click()
                c.locator('#projects-view [data-project="'+vid+'"]').first.click()
                c.locator('[data-detail-tab=scenes]').click()
                c.locator('[data-edit-scene]').first.click()
                c.locator('#scene-form [name=narration]').fill('Lời đọc đã sửa từ phòng dựng.')
                c.locator('#scene-form [type=submit]').click()
                c.locator('#scene-dialog').wait_for(state='hidden')
                deadline = time.monotonic()+45
                while time.monotonic()<deadline:
                    manifest = factory.orch.manifest(vid)
                    if manifest.version>version and any(g['kind']=='publish' for g in factory.snapshot()['gates']): break
                    time.sleep(.1)
                assert manifest.version>version and manifest.scenes[0].narration=='Lời đọc đã sửa từ phòng dựng.'
                c.wait_for_function('(version) => detailProject?.manifest_version > version && latest(detailProject,"final_video")?.manifest_version === detailProject.manifest_version',arg=version)
                c.locator('[data-create-shorts]').click()
                assert c.locator('#shorts-form [name=fit]').input_value()=='reflow'
                assert not c.locator('#shorts-form [name=focus_x]').is_visible()
                c.locator('#shorts-form [name=title]').fill('Shorts tạo qua giao diện')
                c.locator('#shorts-form [name=end_s]').fill('1')
                c.locator('#shorts-form [type=submit]').click()
                c.locator('#shorts-dialog').wait_for(state='hidden')
                deadline = time.monotonic()+45
                while time.monotonic()<deadline:
                    projects=factory.snapshot()['projects']
                    if len(projects)==2 and all('PUB-'+p['id'] in factory.orch.gate.pending for p in projects): break
                    time.sleep(.1)
                assert any(p['title']=='Shorts tạo qua giao diện' for p in projects)
                assert len(projects)==2 and all('PUB-'+p['id'] in factory.orch.gate.pending for p in projects)
                c.locator('#detail-dialog [data-close]').first.click()
                c.locator('.nav[data-view=settings]').click(); c.locator('#doctor-run').click()
                c.locator('#doctor-result').filter(has_text='Môi trường đạt').wait_for()
                c.locator('#resources-run').click()
                c.locator('#resources-result').filter(has_text='USD').wait_for()
                c.locator('#cleanup-preview').click()
                c.locator('#cleanup-result').filter(has_text='segment').wait_for()
                bundle = root / 'bundle'
                c.locator('#backup-form input').fill(str(bundle)); c.locator('#backup-form [type=submit]').click()
                c.locator('#backup-result').filter(has_text=str(bundle)).wait_for(timeout=15000)
                assert (bundle / 'manifest.json').is_file()
                c.screenshot(path=str(screenshots / 'desktop.png'), full_page=True)
                c.set_viewport_size({'width':390, 'height':844})
                assert c.evaluate('document.documentElement.scrollWidth <= innerWidth')
                c.screenshot(path=str(screenshots / 'mobile.png'), full_page=True)
                assert not errors, errors
                browser.close()
            restored = root / 'restored'; restore_backup(root / 'bundle', restored)
            reopened = Factory(restored / 'studio.sqlite', load_media_config(restored / 'media.restore.yaml'), demo=True)
            try:
                assert not reopened.running
                assert len(reopened.ideas.snapshot()['ideas']) == 2
            finally: reopened.close()
        finally:
            factory.stop.set(); worker.join(); server.shutdown(); http.join(); server.server_close(); factory.close()
    print('Operations smoke passed: independent browsers, conflict drafts, import retry, scene edit, Shorts gates, doctor, backup, restore, mobile.')


if __name__ == '__main__': main()
