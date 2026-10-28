import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import uuid
import xml.etree.ElementTree as ET

import cli
import engine

WORK = Path(__file__).resolve().parents[1] / 'test-state' / ('cli-' + uuid.uuid4().hex[:8])
WORK.mkdir(parents=True)


class CLIRegression(unittest.TestCase):
    def setUp(self):
        self.home = WORK / uuid.uuid4().hex[:8]
        self.home.mkdir()
        self.vault = self.home / 'vault'
        (self.vault / '.obsidian').mkdir(parents=True)
        self.change = patch.object(cli, 'HOME', self.home)
        self.change.start()
        cli.init(argparse.Namespace(vault=str(self.vault), hour='23:59', base_url='http://127.0.0.1:3000', worldmonitor_dir=None))

    def tearDown(self):
        self.change.stop()

    def test_due_and_bounded_retry_restart(self):
        now=dt.datetime(2026,1,2,8,tzinfo=engine.TZ)
        cfg=dict(hour='08:00')
        self.assertFalse(cli.due(now-dt.timedelta(seconds=1),cfg,{}))
        self.assertTrue(cli.due(now,cfg,{}))
        s=dict(day='2026-01-02',attempts=1,retry_at=now.timestamp()+900,success=False)
        self.assertFalse(cli.due(now,cfg,s))
        self.assertTrue(cli.due(now+dt.timedelta(seconds=901),cfg,s))
        s['attempts']=3
        self.assertFalse(cli.due(now+dt.timedelta(hours=2),cfg,s))
        self.assertTrue(cli.due(now+dt.timedelta(days=1),cfg,s))
        s['attempts']=1; s['success']=True
        self.assertFalse(cli.due(now+dt.timedelta(hours=2),cfg,s))

    def test_environment_restored_and_probe_needs_no_model_key(self):
        with patch.dict(os.environ,{'WORLDMONITOR_API_KEY':'synthetic-world-key'},clear=True):
            with cli.key_environment(('WORLDMONITOR_API_KEY',)):
                self.assertNotIn('DEEPSEEK_API_KEY',os.environ)
            self.assertEqual(os.environ['WORLDMONITOR_API_KEY'],'synthetic-world-key')
            with self.assertRaises(RuntimeError):cli.credentials()

    def test_startup_failure_always_records_new_failure(self):
        cfg=cli.config()
        engine.save_json(self.home/'latest-run.json',dict(result='failed',error='older failure'))
        with patch.object(cli,'service',side_effect=RuntimeError('synthetic startup timeout')):
            with self.assertRaises(RuntimeError):cli.run(cfg)
        m=json.loads((self.home/'latest-run.json').read_text(encoding='utf-8'))
        self.assertEqual(m['error'],'synthetic startup timeout')
        self.assertEqual(m['stage'],'service-or-credentials')

    @unittest.skipUnless(os.name=='nt','Windows DPAPI integration')
    def test_dpapi_round_trip(self):
        ciphertext=cli.protect('synthetic-only-credential')
        self.assertNotIn('synthetic-only-credential',ciphertext)
        self.assertEqual(cli.unprotect(ciphertext),'synthetic-only-credential')

    @unittest.skipUnless(os.name=='nt','Windows login task XML')
    def test_hidden_login_task_contains_no_credentials(self):
        def run(args,**kwargs):
            return subprocess.CompletedProcess(args,1 if '/Query' in args else 0,stdout=b'',stderr=b'')
        with patch.object(cli.subprocess,'run',side_effect=run):cli.autostart()
        xml=ET.parse(self.home/'login-task.xml'); ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
        argument=xml.find('.//t:Arguments',ns).text
        self.assertIn('-WindowStyle Hidden',argument)
        payload=base64.b64decode(argument.split()[-1]).decode('utf-16-le')
        self.assertIn('WM_OBSIDIAN_HOME',payload)
        self.assertIn('exit $LASTEXITCODE',payload)
        self.assertNotIn('synthetic-world-key',payload)
        self.assertEqual(xml.find('.//t:RunLevel',ns).text,'LeastPrivilege')

    @unittest.skipUnless(os.name=='nt','Windows permission-free startup fallback')
    def test_startup_folder_fallback_is_hidden_and_scoped(self):
        path=self.home/'Startup'/'SyntheticTest.vbs'
        with patch.object(cli,'startup_path',return_value=path), patch.object(cli.subprocess,'run',return_value=subprocess.CompletedProcess([],1,stdout=b'',stderr=b'')):
            cli.autostart()
            self.assertEqual(json.loads((self.home/'autostart.json').read_text(encoding='utf-8'))['method'],'startup-folder')
            text=path.read_text(encoding='utf-16')
            self.assertIn(', 0, False',text)
            self.assertIn('WScript.Sleep 45000',text)
            self.assertNotIn('synthetic-model-key',text)
            # The uninstall path is validated via a mock; tests do not delete user files.
            with patch.object(Path,'unlink') as unlink:
                cli.autostart(uninstall=True)
                unlink.assert_called_once_with()

    def test_default_background_start_status_stop(self):
        now=dt.datetime.now(engine.TZ)
        if now.hour==23 and now.minute>=58:
            self.skipTest('Avoid crossing the synthetic scheduled time in this smoke test')
        env=dict(os.environ,WM_OBSIDIAN_HOME=str(self.home),PYTHONIOENCODING='utf-8',
                 WORLDMONITOR_API_KEY='synthetic-world-key',DEEPSEEK_API_KEY='synthetic-model-key')
        def call(*args):
            r=subprocess.run([sys.executable,str(cli.SCRIPT),*args],env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
            self.assertEqual(r.returncode,0,r.stderr)
            return json.loads(r.stdout)
        try:
            self.assertEqual(call()['result'],'started-in-background')
            self.assertEqual(call('start')['result'],'already-running')
            self.assertTrue(call('status')['background_running'])
        finally:
            call('stop')
        for _ in range(30):
            if not call('status')['background_running']:break
            time.sleep(.25)
        self.assertFalse(call('status')['background_running'])
        self.assertFalse(list((self.home/'runs').glob('*')))

    def test_refuse_http_remote_and_folder_traversal(self):
        for override in [dict(base_url='http://example.com'),dict(folder='../outside')]:
            local=json.loads((self.home/'config.json').read_text(encoding='utf-8'))
            engine.save_json(self.home/'config.json',dict(local,**override))
            with self.assertRaises(ValueError):cli.config()
            engine.save_json(self.home/'config.json',local)
