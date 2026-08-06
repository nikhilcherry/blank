"""The README makes promises. These check the CLI can keep them.

Documentation drift is silent: a flag gets renamed, the README keeps the old
name, and nobody notices until someone copies a command that no longer exists.
"""

import re
import shlex
import unittest
from pathlib import Path

from blank.cli import build_parser

README = Path(__file__).resolve().parents[1] / "README.md"


def documented_commands():
    """Every `blank ...` invocation inside a bash block in the README."""
    text = README.read_text(encoding="utf-8")
    found = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        for line in block.splitlines():
            line = line.split("#")[0].strip()
            if line.startswith("blank "):
                # Stop at a shell operator; the rest is not blank's business.
                for stop in ("|", ">>", ">"):
                    line = line.split(stop)[0]
                found.append(line.strip())
    return list(dict.fromkeys(found))


class ReadmeTests(unittest.TestCase):
    def test_readme_exists_and_has_examples(self):
        self.assertTrue(README.exists())
        self.assertGreaterEqual(len(documented_commands()), 8)

    def test_every_documented_command_parses(self):
        parser = build_parser()
        for command in documented_commands():
            args = shlex.split(command)[1:]  # drop the "blank" argv[0]
            with self.subTest(command=command):
                try:
                    parser.parse_args(args)
                except SystemExit as exc:  # argparse exits on a bad flag
                    self.fail(f"README documents a command the CLI rejects: {command} ({exc})")

    def test_every_subcommand_is_documented(self):
        parser = build_parser()
        subparsers = [
            action for action in parser._actions
            if hasattr(action, "choices") and isinstance(action.choices, dict)
        ]
        self.assertTrue(subparsers, "no subparsers found")
        names = set(subparsers[0].choices)
        text = README.read_text(encoding="utf-8")
        for name in names:
            with self.subTest(subcommand=name):
                self.assertIn(f"blank {name}", text, f"`blank {name}` is undocumented")

    def test_no_stale_metric_language(self):
        # The ranking moved from churn to revisions; the phrase must not return.
        text = README.read_text(encoding="utf-8")
        self.assertNotIn("churn × complexity", text)
        self.assertNotIn("normalise(churn)", text)

    def test_referenced_images_exist(self):
        text = README.read_text(encoding="utf-8")
        for path in set(re.findall(r'src="(docs/img/[^"]+)"', text)):
            with self.subTest(image=path):
                self.assertTrue((README.parent / path).exists(), path)

    def test_referenced_repo_files_exist(self):
        text = README.read_text(encoding="utf-8")
        targets = re.findall(r"\]\((blank/[\w./]+|LICENSE|pyproject\.toml|action\.yml)\)", text)
        self.assertTrue(targets)
        for target in set(targets):
            with self.subTest(link=target):
                self.assertTrue((README.parent / target).exists(), target)


if __name__ == "__main__":
    unittest.main()
