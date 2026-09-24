import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_public_files import audit_repository, check_blob


class PublicationTests(unittest.TestCase):
    def test_personal_materials_and_symlinks_rejected(self):
        for path in ("private-video/manifest.json", "tool/.state/jobs/task.json", "tool/.env"):
            self.assertTrue(check_blob(path, "100644", b"{}"))
        self.assertTrue(check_blob("README.md", "120000", b"external-file"))
        self.assertTrue(check_blob("README.md", "100644", b"\0binary"))

    def test_sensitive_values_report_labels_only(self):
        examples = ["sk-" + "x" * 40, "ghp_" + "x" * 40,
                    "/Users/" + "example-person/private/", "Bearer " + "x" * 40,
                    '"access_token": "' + "x" * 40 + '"']
        for value in examples:
            with self.subTest(kind=value[:3]):
                issues = check_blob("README.md", "100644", value.encode())
                self.assertTrue(issues)
                self.assertNotIn(value, " ".join(issues))
        self.assertEqual(check_blob("README.md", "100644", b"A local learning tool."), [])

    def test_audit_reads_staged_blob_instead_of_clean_working_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args):
                return subprocess.check_output(["git", "-C", folder, *args], stderr=subprocess.PIPE)
            git("init", "--quiet")
            source = root / "README.md"
            source.write_text("sk-" + "x" * 40)
            git("add", "README.md")
            source.write_text("Clean working copy, unsafe index.")
            self.assertTrue(audit_repository(root)[1])
            git("add", "README.md")
            self.assertEqual(audit_repository(root), (1, []))
            tree = git("write-tree").decode().strip()
            self.assertEqual(audit_repository(root, tree), (1, []))
            private = root / "private-video"
            private.mkdir()
            (private / "manifest.json").write_text("{}")
            git("add", "private-video")
            self.assertTrue(audit_repository(root)[1])


if __name__ == "__main__":
    unittest.main()
