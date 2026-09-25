"""Subtree provenance must survive freezing and ignore unrelated app edits."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'builder' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class RelocationTest(unittest.TestCase):
    def test_subtree_manifest_and_frozen_history(self):
        checkpoint = module('asahi_checkpoint')
        stages = module('asahi_stage_inputs')
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'repository'
            source = repo / 'image-builder'
            source.mkdir(parents=True)
            (source / 'input').write_text('builder input\n')
            (repo / 'app.swift').write_text('app input\n')
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], text=True)
            git('init', '-q')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'commit.gpgsign', 'false')
            git('add', '.')
            git('commit', '-qm', 'fixture')
            declaration = {'source_paths': ['input'], 'runtime_paths': [],
                           'runtime_settings': [], 'depends_on': []}
            before = stages.build_stage_source_manifest(source, 'fixture', ['input'], declaration)
            checkpoint.build_source_manifest(source, ['input'])
            (repo / 'app.swift').write_text('changed app\n')
            git('add', 'app.swift')
            git('commit', '-qm', 'app only')
            self.assertEqual(before, stages.build_stage_source_manifest(source, 'fixture', ['input'], declaration))
            frozen = Path(directory) / 'frozen'
            shutil.copytree(repo / '.git', frozen / '.git')
            shutil.copytree(source, frozen / 'image-builder')
            self.assertEqual(before, stages.build_stage_source_manifest(frozen / 'image-builder', 'fixture', ['input'], declaration))
            (source / 'input').write_text('changed builder\n')
            after = stages.build_stage_source_manifest(source, 'fixture', ['input'], declaration)
            self.assertNotEqual(before['source_identity'], after['source_identity'])
            self.assertTrue(after['status'])
            with self.assertRaises(checkpoint.CheckpointError):
                checkpoint.build_source_manifest(source, ['../app.swift'])
            (source / 'link').symlink_to(repo / 'app.swift')
            with self.assertRaises(checkpoint.CheckpointError):
                checkpoint.build_source_manifest(source, ['link'])


if __name__ == '__main__':
    unittest.main()
