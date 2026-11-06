"""Integration checks use the real vendor deb when ATRUST_TEST_DEB is set.

Run: python3 -m unittest discover -s tests -v
Full: ATRUST_TEST_DEB=$PWD/aTrustInstaller_amd64.deb python3 -m unittest discover -s tests -v
"""

import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock

import adapt_atrust_debian as adapter


SCRIPT = Path(adapter.__file__).resolve()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class CommandLineTests(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run(["python3", str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True)

    def test_help_and_invalid_arguments(self):
        self.assertEqual(self.cli("--help").returncode, 0)
        self.assertEqual(self.cli("--unknown").returncode, 2)

    def test_missing_input_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.deb"
            result = self.cli(Path(directory) / "missing.deb", "-o", output)
            self.assertEqual(result.returncode, 1)
            self.assertIn("找不到输入包", result.stderr)
            self.assertFalse(output.exists())

    def test_output_conflict_preserves_both_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.deb"
            output = Path(directory) / "output.deb"
            source.write_bytes(b"original")
            output.write_bytes(b"existing")
            self.assertIn("拒绝覆盖", self.cli(source, "-o", output).stderr)
            self.assertIn("拒绝覆盖", self.cli(source, "-o", source).stderr)
            self.assertEqual(source.read_bytes(), b"original")
            self.assertEqual(output.read_bytes(), b"existing")

    def test_dangling_output_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.deb"
            source.touch()
            target = Path(directory) / "nonexistent"
            output = Path(directory) / "output.deb"
            output.symlink_to(target)
            self.assertIn("拒绝覆盖", self.cli(source, "-o", output).stderr)
            self.assertFalse(target.exists())

    def test_wrong_identity_is_rejected_before_unpack(self):
        for field, value in (("Architecture", "arm64"), ("Version", "2.5.16.51"),
                             ("Package", "another.package")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                package = root / "fixture"
                (package / "DEBIAN").mkdir(parents=True)
                fields = {"Package": adapter.PACKAGE, "Version": adapter.VERSION,
                          "Architecture": "amd64", "Maintainer": "Test <test@example.com>",
                          "Description": "Unsupported input fixture"}
                fields[field] = value
                (package / "DEBIAN/control").write_text(
                    "".join(f"{k}: {v}\n" for k, v in fields.items()))
                source = root / "input.deb"
                adapter.run("dpkg-deb", "--build", str(package), str(source))
                output = root / "output.deb"
                result = self.cli(source, "-o", output)
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"不支持的 {field}", result.stderr)
                self.assertFalse(output.exists())
                self.assertFalse(list(root.glob(".atrust-repack-*")))

    def test_modified_structure_fails_and_cleans_temporary_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "fixture"
            (package / "DEBIAN").mkdir(parents=True)
            (package / "DEBIAN/control").write_text(
                f"Package: {adapter.PACKAGE}\nVersion: {adapter.VERSION}\nArchitecture: amd64\n"
                "Maintainer: Test <test@example.com>\nDescription: Missing hooks\n")
            source = root / "input.deb"
            adapter.run("dpkg-deb", "--build", str(package), str(source))
            output = root / "output.deb"
            result = self.cli(source, "-o", output)
            self.assertEqual(result.returncode, 1)
            self.assertIn("安装包结构不受支持", result.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob(".atrust-repack-*")))


@unittest.skipUnless(os.environ.get("ATRUST_TEST_DEB"), "设置 ATRUST_TEST_DEB 可运行真实包集成测试")
class RealPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vendor = Path(os.environ["ATRUST_TEST_DEB"]).resolve()
        # The fixture holds two extracted packages; /tmp may be a small tmpfs.
        cls.temp = tempfile.TemporaryDirectory(prefix=".atrust-integration- ", dir=cls.vendor.parent)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.before = digest(cls.vendor)
        cls.source = cls.directory / "aTrust installer '$(touch INJECTION)'_amd64.deb"
        cls.source.symlink_to(cls.vendor)
        cls.output = cls.directory / "adapted package.deb"
        adapter.adapt(cls.source, cls.output)
        cls.original = cls.directory / "original"
        cls.adapted = cls.directory / "adapted"
        adapter.run("dpkg-deb", "-R", str(cls.vendor), str(cls.original))
        adapter.run("dpkg-deb", "-R", str(cls.output), str(cls.adapted))

    def test_input_unchanged_and_output_identity(self):
        self.assertEqual(digest(self.vendor), self.before)
        fields = adapter.control_fields(adapter.run("dpkg-deb", "-f", str(self.output)))
        self.assertEqual(fields["Package"], adapter.PACKAGE)
        self.assertEqual(fields["Version"], adapter.ADAPTED_VERSION)
        self.assertEqual(fields["Architecture"], "amd64")
        self.assertIn("libqt5core5t64", fields["Depends"])
        self.assertIn("libstdc++6 (>= 14)", fields["Depends"])

    def test_untouched_payload_is_identical_including_symlinks_and_modes(self):
        changed = set(adapter.DESKTOPS) | {adapter.SHELL / "aTrustTrayStart.sh",
                                         adapter.SHELL / "aTrustDaemon.sh"}
        removed = {adapter.RES / "lib" / n for n in adapter.REMOVE_LIBS}
        removed.add(adapter.APP / "uem/lib/libstdc++.so.6")
        for original in self.original.rglob("*"):
            relative = original.relative_to(self.original)
            if relative.parts[0] == "DEBIAN" or relative in changed:
                continue
            adapted = self.adapted / relative
            with self.subTest(path=str(relative)):
                if relative in removed:
                    self.assertFalse(adapted.exists())
                elif original.is_symlink():
                    self.assertEqual(os.readlink(original), os.readlink(adapted))
                elif original.is_file():
                    self.assertEqual(digest(original), digest(adapted))
                    self.assertEqual(original.stat().st_mode, adapted.stat().st_mode)

    def test_original_filename_is_safe_shell_literal(self):
        text = (self.adapted / "DEBIAN/postinst").read_text()
        # Execute only the isolated assignment function, never the vendor postinst.
        start = text.index("get_package_file_name() {\n")
        end = text.index("\n}", start) + 2
        script = text[start:end] + '\nget_package_file_name\nprintf "%s" "$package_file_name"\n'
        result = subprocess.run(["bash", "-c", script], cwd=self.directory,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, self.source.name)
        self.assertFalse((self.directory / "INJECTION").exists())

    def test_debian_13_uses_systemd_without_ubuntu_version_threshold(self):
        text = (self.adapted / "DEBIAN/postinst").read_text()
        functions = []
        for name in ("detect_distro", "add_service"):
            start = text.index("\n" + name + "() {\n") + 1
            end = text.index("\n}", start) + 2
            functions.append(text[start:end])
        script = '\n'.join(functions) + '''
system_add_service() { printf systemd; }
upstart_add_service() { printf upstart; }
detect_distro
add_service
'''
        release = self.directory / "os-release"
        release.write_text('ID=debian\nVERSION_ID="13"\n')
        result = subprocess.run(["bash", "-c", script.replace("/etc/os-release", shlex.quote(str(release)))],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, "systemd")

    def test_install_guard_rejects_other_distributions_before_vendor_code(self):
        text = (self.adapted / "DEBIAN/preinst").read_text()
        guard = text[:text.index('echo "all argv $*"')]
        release = self.directory / "guard-os-release"
        for distro, version, expected in (("debian", "13", 0), ("debian", "12", 1),
                                         ("ubuntu", "24.04", 1)):
            with self.subTest(distro=distro, version=version):
                release.write_text(f'ID={distro}\nVERSION_ID="{version}"\n')
                script = guard.replace("/etc/os-release", shlex.quote(str(release)))
                result = subprocess.run(["bash", "-c", script, "preinst", "install"],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, expected)

    def test_shell_syntax_checksums_and_desktop(self):
        adapter.validate_scripts(self.adapted)
        result = subprocess.run(["md5sum", "--check", "--quiet", "DEBIAN/md5sums"],
                                cwd=self.adapted, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for relative in adapter.DESKTOPS:
            text = (self.adapted / relative).read_text()
            self.assertEqual(text.count("Exec="), 1)
            self.assertIn("aTrustDebianExec.sh", text)
        for name in ("common", "preinst", "postinst", "prerm"):
            self.assertNotIn("lsb_release", (self.adapted / "DEBIAN" / name).read_text())

    def test_unknown_library_is_rejected(self):
        with mock.patch.object(adapter, "elf_inventory", return_value={
                self.adapted / "usr/bin/unknown": ({"libunexpected.so.99"}, set())}):
            with self.assertRaisesRegex(adapter.AdaptError, "libunexpected.so.99"):
                adapter.dependencies(self.adapted)

    def test_missing_patch_anchor_is_rejected(self):
        # Actual hook content, with an upstream change at the package-name function.
        text = (self.original / "DEBIAN/postinst").read_text()
        changed = text.replace("get_package_file_name() {", "renamed_upstream() {")
        with self.assertRaisesRegex(adapter.AdaptError, "函数补丁匹配失败"):
            adapter.replace_function(changed, "get_package_file_name", "  return 0")

if __name__ == "__main__":
    unittest.main()
