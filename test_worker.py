import base64
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import worker


def archive(entries):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        for name, value in entries:
            z.writestr(name, value)
    return base64.b64encode(data.getvalue()).decode()


class WorkerTests(unittest.TestCase):
    def test_diagnostics_are_private_and_redacted(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);repo=root/'source';repo.mkdir()
            with (root/'private.log').open('wb') as log:
                log.write(b'FAILED scene 2: invalid motion; gsk_privatekey123 secret-value')
                with patch.dict(os.environ,{'SOURCE_TOKEN':'secret-value'}):
                    worker.save_diagnostics(repo,log,['writing'],'writing')
            text=(repo/'state/worker/latest.log').read_text()
            self.assertIn('invalid motion',text)
            self.assertNotIn('gsk_privatekey123',text)
            self.assertNotIn('secret-value',text)


    def test_groq_only_mode_does_not_touch_production(self):
        with patch.object(worker.sys, 'argv', ['worker.py', '--check-groq']), \
             patch.dict(os.environ, {'GROQ_API_KEY': 'test-key'}, clear=True), \
             patch.object(worker, 'verify_groq', return_value=True), \
             patch.object(worker, 'run') as run:
            self.assertEqual(worker.main(), 0)
        run.assert_not_called()

    def test_groq_check_authenticates_without_generating_text(self):
        with patch.object(worker.urllib.request, 'urlopen') as request, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            request.return_value.__enter__.return_value = io.BytesIO(b'{"data":[{"id":"model"}]}')
            self.assertTrue(worker.verify_groq('test-key'))
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, 'https://api.groq.com/openai/v1/models')
        self.assertEqual(req.get_method(), 'GET')
        self.assertNotIn('test-key', out.getvalue())

    def test_groq_failure_does_not_print_exception_or_key(self):
        with patch.object(worker.urllib.request, 'urlopen', side_effect=RuntimeError('test-key')), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertFalse(worker.verify_groq('test-key'))
        self.assertNotIn('test-key', out.getvalue())

    def test_expected_credentials_only(self):
        encoded = archive([('client_secret.json', '{}'), ('youtube_deep-sea-files.json', '{}')])
        with tempfile.TemporaryDirectory() as t:
            target = Path(t) / 'secrets'
            self.assertEqual(set(worker.unpack_credentials(encoded, target)),
                             {'client_secret.json', 'youtube_deep-sea-files.json'})
            self.assertEqual(json.loads((target / 'youtube_deep-sea-files.json').read_text()), {})

    def test_rejects_archive_traversal_before_writing(self):
        with tempfile.TemporaryDirectory() as t:
            target = Path(t) / 'secrets'
            with self.assertRaises(ValueError):
                worker.unpack_credentials(archive([('../outside.json', '{}')]), target)
            self.assertFalse(target.exists())

    def test_rejects_code_in_credential_archive(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                worker.unpack_credentials(archive([('script.py', 'print(1)')]), Path(t))

    def test_rejects_invalid_credential_json(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                worker.unpack_credentials(archive([('youtube_deep-sea-files.json', 'invalid')]), Path(t))

    def test_token_is_scoped_to_github_and_noninteractive(self):
        env = worker.git_env('example-token')
        config = {env[f'GIT_CONFIG_KEY_{i}']: env[f'GIT_CONFIG_VALUE_{i}']
                  for i in range(int(env['GIT_CONFIG_COUNT']))}
        self.assertEqual(config['credential.helper'], '')
        self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')
        self.assertIn('http.https://github.com/.extraheader', config)

    def test_no_credentials_means_no_network_or_render(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(worker, 'run') as run:
            self.assertEqual(worker.main(), 2)
        run.assert_not_called()

    def test_production_output_is_private_and_token_not_passed_to_renderer(self):
        secret = 'PRIVATE-STORY-SENTINEL'
        credentials = archive([('youtube_deep-sea-files.json', '{}')])
        calls = []

        def fake_run(args, cwd, log, env=None, **kwargs):
            calls.append((args, env))
            log.write(secret.encode())
            if args[:2] == ['git', 'clone']:
                repo = Path(args[-1])
                (repo / 'inbox').mkdir(parents=True)
                (repo / 'inbox/2026-10-29_deep-sea-files_1.json').write_text('{}')
            return 0

        with patch.dict(os.environ, {'SOURCE_TOKEN': 'repo-secret', 'YT_SECRETS': credentials,
                                    'GITHUB_STEP_SUMMARY': '/public-summary', 'GITHUB_OUTPUT': '/public-output',
                                    'GITHUB_ENV': '/public-env', 'GITHUB_PATH': '/public-path'}, clear=True), \
             patch.object(worker, 'run', side_effect=fake_run), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(worker.main(), 0)
        self.assertNotIn(secret, out.getvalue())
        self.assertNotIn('repo-secret', out.getvalue())
        for args, env in calls:
            if args[:2] == [worker.sys.executable, 'hub.py']:
                self.assertNotIn('SOURCE_TOKEN', env)
                self.assertNotIn('YT_SECRETS', env)
                for name in ('GITHUB_STEP_SUMMARY', 'GITHUB_OUTPUT', 'GITHUB_ENV', 'GITHUB_PATH'):
                    self.assertNotIn(name, env)
        self.assertTrue(any(args==[worker.sys.executable,'hub.py','cloud','--channel','deep-sea-files'] for args,_ in calls))
        cloud_index=next(i for i,(args,_) in enumerate(calls) if 'cloud' in args)
        self.assertTrue(any(args[:3]==['git','add','-A'] for args,_ in calls[cloud_index+1:]))
        dashboard=[env for args,env in calls if args==['bash','tools/publish_dashboard.sh']]
        self.assertEqual(len(dashboard),1)
        self.assertEqual(dashboard[0]['PAGES_TOKEN'],'repo-secret')
        self.assertNotIn('GROQ_API_KEY',dashboard[0])


class IncrementalUploads(unittest.TestCase):
    def test_each_channel_is_checkpointed_before_the_next_render(self):
        events=[]
        def fake_run(args,cwd,log,env=None,**kwargs):
            if args[:2]==['git','clone']:
                inbox=Path(args[-1])/'inbox';inbox.mkdir(parents=True)
                for cid in ('a','b'):(inbox/f'2026-10-30_{cid}_1.json').write_text('{}')
            if args[:3]==[worker.sys.executable,'hub.py','cloud']:events.append(args[-1])
            return 0
        credentials=archive([('youtube_a.json','{}'),('youtube_b.json','{}')])
        with patch.dict(os.environ,{'SOURCE_TOKEN':'test','YT_SECRETS':credentials},clear=True),patch.object(worker,'run',side_effect=fake_run),patch.object(worker,'save_private',side_effect=lambda *args:events.append('save')),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(worker.main(),0)
        first=events.index('a');second=events.index('b')
        self.assertIn('save',events[first+1:second])


class CatchUpTests(unittest.TestCase):
    def test_cloud_handoff_validation_runs_without_groq(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td);(repo/'tools').mkdir();(repo/'tools/cloud_writer.py').write_text('')
            with (repo/'log').open('wb') as log,patch.object(worker,'run',return_value=0) as run,patch.object(worker,'save_diagnostics'),patch.object(worker,'save_private'),contextlib.redirect_stdout(io.StringIO()):
                worker.catch_up(repo,log,{}, {},[],False)
            self.assertEqual([c.args[0] for c in run.call_args_list],[[worker.sys.executable,'tools/local_writer.py','--backend','groq']])

    def test_primary_cloud_mode_needs_no_groq_key_or_authentication(self):
        calls=[]
        def fake_run(args,cwd,log,env=None,**kwargs):
            calls.append((args,env))
            if args[:2]==['git','clone']:
                repo=Path(args[-1]);(repo/'tools').mkdir(parents=True)
                (repo/'tools/cloud_writer.py').write_text('')
                (repo/'work/cloud-writer').mkdir(parents=True)
                (repo/'work/cloud-writer/config.json').write_text('{"mode":"cloud"}')
            return 0
        credentials=archive([('youtube_a.json','{}')])
        with patch.dict(os.environ,{'SOURCE_TOKEN':'test','YT_SECRETS':credentials,'GROQ_API_KEY':'expired'},clear=True),patch.object(worker,'run',side_effect=fake_run),patch.object(worker,'verify_groq') as auth,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(worker.main(),0)
        auth.assert_not_called()
        checks=[env for args,env in calls if 'unittest' in args]
        self.assertNotIn('SHORTS_GROQ_AVAILABLE',checks[0])
        writers=[env for args,env in calls if 'tools/local_writer.py' in args]
        self.assertEqual(writers[0]['SHORTS_GROQ_AVAILABLE'],'0')

    def test_delayed_wakeup_writes_and_uploads_four_channels_in_order(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td);(repo/'inbox').mkdir();(repo/'secrets').mkdir()
            events=[];attempts={}
            def fake_run(args,cwd,log,env=None,**kwargs):
                if 'tools/local_writer.py' in args:
                    cid=str(len(attempts));attempts[cid]='attempted'
                    (repo/f'secrets/youtube_{cid}.json').write_text('{}')
                    (repo/f'inbox/2026-10-31_{cid}_1.json').write_text('{}')
                    events.append('write '+cid)
                if 'cloud' in args:
                    cid=args[-1];events.append('upload '+cid)
                    (repo/f'inbox/2026-10-31_{cid}_1.json').unlink()
                return 0
            with (repo/'log').open('wb') as log,patch.object(worker,'run',side_effect=fake_run),patch.object(worker,'writer_attempts',side_effect=lambda _:dict(attempts)),patch.object(worker,'save_diagnostics'),patch.object(worker,'save_private',side_effect=lambda *a:events.append('save')),contextlib.redirect_stdout(io.StringIO()):
                worker.catch_up(repo,log,{}, {},[],True)
            self.assertEqual([e for e in events if e.startswith(('write','upload'))],sum(([f'write {i}',f'upload {i}'] for i in range(4)),[]))
            self.assertIn('save',events[events.index('upload 0')+1:events.index('write 1')])

    def test_quota_or_audience_wait_does_not_spin(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td)
            with (repo/'log').open('wb') as log,patch.object(worker,'run',return_value=0) as run,patch.object(worker,'save_diagnostics'),patch.object(worker,'save_private'),contextlib.redirect_stdout(io.StringIO()):
                worker.catch_up(repo,log,{}, {},[],True)
            self.assertEqual(run.call_count,1)

    def test_no_new_changes_still_retries_private_push(self):
        calls=[]
        with tempfile.TemporaryDirectory() as td,patch.object(worker,'run',side_effect=lambda args,*a,**k:calls.append(args) or 0):
            worker.save_private(Path(td),None,{})
        self.assertIn(['git','push','origin','HEAD:main'],calls)
        self.assertFalse(any('commit' in args for args in calls))

if __name__ == '__main__':
    unittest.main()
