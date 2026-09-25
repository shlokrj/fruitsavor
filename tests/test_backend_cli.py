import contextlib
import io
import unittest
from unittest.mock import patch

from fruitsavor.backend.cli import main


class BackendCLITests(unittest.TestCase):
    def test_default_starts_loopback_factory(self):
        with patch.dict('os.environ', {}, clear=True), patch('fruitsavor.backend.cli.uvicorn.run') as run:
            main([])
        self.assertEqual(run.call_args.kwargs['host'], '127.0.0.1')
        self.assertTrue(run.call_args.kwargs['factory'])
        self.assertEqual(run.call_args.kwargs['limit_concurrency'], 32)

    def test_public_binding_needs_token(self):
        with patch.dict('os.environ', {}, clear=True), patch('fruitsavor.backend.cli.uvicorn.run') as run:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['--host', '0.0.0.0'])
            run.assert_not_called()
        with patch.dict('os.environ', {'FRUITSAVOR_API_TOKEN':'test-only'}, clear=True):
            with patch('fruitsavor.backend.cli.uvicorn.run') as run:
                main(['--host', '0.0.0.0'])
                run.assert_called_once()
