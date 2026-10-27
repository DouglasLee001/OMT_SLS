"""Exercise the real native recipes and incremental dependency graph."""
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "NIA_LS/src"
Z3 = ROOT / "competitor/z3-5.1.0"


class NativeZ3VersionTests(unittest.TestCase):
    def make(self, directory, *arguments):
        result = subprocess.run(
            ["make", "--no-print-directory", "-n", "Z3_ROOT=" + str(Z3), *arguments], cwd=directory,
            text=True, capture_output=True, check=True)
        return [shlex.split(line) for line in result.stdout.splitlines()
                if line.startswith("g++ ")]

    def assert_pinned_headers(self, command):
        includes = {Path(token[2:]).resolve() for token in command
                    if token.startswith("-I") and len(token) > 2}
        self.assertIn(Z3 / "source/src/api", includes)
        self.assertIn(Z3 / "source/src/api/c++", includes)

    def test_release_and_native_test_recipes_use_pinned_sdk(self):
        commands = self.make(SRC, "-B", "all", "test-omt-optimality",
                             "test-omt-import", "scoring-drivers",
                             "lia-scoring-drivers")
        self.assertTrue(commands)
        for command in commands:
            with self.subTest(command=command):
                self.assert_pinned_headers(command)
                if "-c" in command:
                    continue
                self.assertIn("-L" + str(Z3 / "build"), command)
                self.assertIn("-l:libz3.so.5.1.0.0", command)
                self.assertNotIn("-lz3", command)
                self.assertTrue(any("$ORIGIN/lib" in token for token in command))

    def test_makefile_and_sdk_changes_invalidate_existing_outputs(self):
        with tempfile.TemporaryDirectory(prefix="nia-z3-deps-") as temporary:
            repo = Path(temporary)
            directory = repo / "NIA_LS/src"
            directory.mkdir(parents=True)
            (repo / "competitor").symlink_to(ROOT / "competitor", target_is_directory=True)
            shutil.copyfile(SRC / "Makefile", directory / "Makefile")
            for path in [*SRC.glob("*.cpp"), *SRC.glob("*.h")]:
                (directory / path.name).symlink_to(path)
            # Existing outputs newer than every input isolate the tested edge.
            objects = []
            for source in SRC.glob("*.cpp"):
                obj = directory / (source.stem + ".o")
                obj.touch()
                objects.append(obj.name)
            (directory / "nia_ls").touch()
            self.assertEqual(self.make(directory, "all"), [])
            for changed in ("Makefile", str(Z3 / "source/src/api/c++/z3++.h")):
                with self.subTest(changed=changed):
                    commands = self.make(directory, "-W", changed, "all")
                    rebuilt = {Path(command[command.index("-c") + 1]).stem + ".o"
                               for command in commands if "-c" in command}
                    self.assertEqual(rebuilt, set(objects))
            linked = self.make(directory, "-W", str(Z3 / "build/libz3.so.5.1.0.0"), "all")
            self.assertEqual(len(linked), 1)
            self.assertNotIn("-c", linked[0])

    def test_linked_c_api_is_510_in_live_and_frozen_layouts(self):
        link = next(command for command in self.make(SRC, "-B", "all")
                    if "-c" not in command)
        with tempfile.TemporaryDirectory(prefix="nia-z3-version-") as temporary:
            source = Path(temporary) / "run/source"
            source.mkdir(parents=True)
            probe = source / "version-probe"
            output = link.index("-o") + 1
            link[output] = str(probe)
            link = [token for token in link if not token.endswith(".o")]
            completed = subprocess.run(
                [link[0], "-x", "c++", "-", *link[1:]], cwd=SRC, text=True, capture_output=True,
                input="#include <z3.h>\n#include <cstdio>\n"
                      "int main() { puts(Z3_get_full_version()); }\n")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            live = subprocess.check_output([str(probe)], text=True).strip()
            self.assertRegex(live, r"^(?:Z3 )?5\.1\.0(?:\D|$)")
            library_dir = source / "lib"
            library_dir.mkdir()
            frozen_library = library_dir / "libz3.so.5.1"
            shutil.copyfile(Z3 / "build/libz3.so.5.1.0.0", frozen_library)
            dependencies = subprocess.check_output(["ldd", str(probe)], text=True)
            self.assertIn(str(frozen_library), dependencies)
            self.assertNotIn("libz3.so.4", dependencies)
            frozen = subprocess.check_output([str(probe)], text=True).strip()
            self.assertEqual(frozen, live)


if __name__ == "__main__":
    unittest.main()
