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

        with patch.dict(os.environ, {'SOURCE_TOKEN': 'repo-secret', 'YT_SECRETS': credentials}, clear=True), \
             patch.object(worker, 'run', side_effect=fake_run), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(worker.main(), 0)
        self.assertNotIn(secret, out.getvalue())
        self.assertNotIn('repo-secret', out.getvalue())
        for args, env in calls:
            if args[:2] == [worker.sys.executable, 'hub.py']:
                self.assertNotIn('SOURCE_TOKEN', env)
                self.assertNotIn('YT_SECRETS', env)
        self.assertTrue(any('cloud' in args for args, _ in calls))


if __name__ == '__main__':
    unittest.main()
