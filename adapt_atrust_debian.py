#!/usr/bin/env python3
"""Repack the supported aTrust Ubuntu deb for Debian 13, without installing it."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


PACKAGE = "cn.com.sangfor.atrust"
VERSION = "2.5.16.50"
ADAPTED_VERSION = VERSION + "+debian13.1"
APP = Path("usr/share/sangfor/aTrust")
RES = APP / "resources"
SHELL = RES / "shell"
PLUGIN_DIR = "/usr/lib/x86_64-linux-gnu/qt5/plugins"
ENVIRONMENT = f'''# aTrust Debian 13: scoped to the client and its child processes.
export LD_LIBRARY_PATH=/usr/share/sangfor/aTrust:/usr/share/sangfor/aTrust/resources/bin:/usr/share/sangfor/aTrust/resources/lib
export QT_PLUGIN_PATH={PLUGIN_DIR}
export QT_QPA_PLATFORM_PLUGIN_PATH={PLUGIN_DIR}/platforms
'''
ENV_SOURCE = 'source /usr/share/sangfor/aTrust/resources/shell/aTrustDebianEnv.sh\n'
DESKTOPS = (
    Path("usr/share/applications/cn.com.sangfor.atrust.desktop"),
    Path("opt/apps/cn.com.sangfor.atrust/entries/applications/cn.com.sangfor.atrust.desktop"),
)
# Exact allowlist: do not remove other vendor libraries, especially OpenSSL 1.1.
REMOVE_LIBS = (
    "libQt5Core.so.5", "libQt5DBus.so.5", "libQt5Gui.so.5",
    "libQt5Network.so.5", "libQt5Widgets.so.5", "libQt5X11Extras.so.5",
    "libQt5XcbQpa.so.5", "libqxcb.so", "libstdc++.so.6",
    "libicudata.so.55", "libicui18n.so.55", "libicuuc.so.55",
    "libpcre16.so.3", "libpng12.so.0", "libxcb.so.1", "libxcb-xinerama.so.0",
    "libldap_r-2.4.so.2", "liblber-2.4.so.2", "libgssapi.so.3",
    "libkrb5.so.26", "libasn1.so.8", "libheimntlm.so.0", "libhcrypto.so.4",
    "libroken.so.18", "libwind.so.0", "libheimbase.so.1", "libhx509.so.5",
)
# Debian 13 package names for external DT_NEEDED entries. Unknown names fail closed.
SONAME_PACKAGES = {
    **dict.fromkeys(("ld-linux-x86-64.so.2", "libc.so.6", "libm.so.6",
                     "libpthread.so.0", "libdl.so.2", "librt.so.1", "libresolv.so.2"),
                    "libc6 (>= 2.41)"),
    "libgcc_s.so.1": "libgcc-s1", "libstdc++.so.6": "libstdc++6 (>= 14)",
    "libQt5Core.so.5": "libqt5core5t64", "libQt5DBus.so.5": "libqt5dbus5t64",
    "libQt5Gui.so.5": "libqt5gui5t64", "libQt5Network.so.5": "libqt5network5t64",
    "libQt5Widgets.so.5": "libqt5widgets5t64", "libQt5X11Extras.so.5": "libqt5x11extras5",
    "libX11.so.6": "libx11-6", "libX11-xcb.so.1": "libx11-xcb1",
    "libXcomposite.so.1": "libxcomposite1", "libXcursor.so.1": "libxcursor1",
    "libXdamage.so.1": "libxdamage1", "libXext.so.6": "libxext6",
    "libXfixes.so.3": "libxfixes3", "libXi.so.6": "libxi6",
    "libXrandr.so.2": "libxrandr2", "libXrender.so.1": "libxrender1",
    "libXss.so.1": "libxss1", "libXtst.so.6": "libxtst6",
    "libasound.so.2": "libasound2t64", "libatk-1.0.so.0": "libatk1.0-0t64",
    "libatk-bridge-2.0.so.0": "libatk-bridge2.0-0t64",
    "libatspi.so.0": "libatspi2.0-0t64", "libcairo.so.2": "libcairo2",
    "libcups.so.2": "libcups2t64", "libdbus-1.so.3": "libdbus-1-3",
    "libdrm.so.2": "libdrm2", "libexpat.so.1": "libexpat1", "libgbm.so.1": "libgbm1",
    "libgdk-3.so.0": "libgtk-3-0t64", "libgtk-3.so.0": "libgtk-3-0t64",
    "libgdk_pixbuf-2.0.so.0": "libgdk-pixbuf-2.0-0",
    **dict.fromkeys(("libgio-2.0.so.0", "libglib-2.0.so.0", "libgobject-2.0.so.0"),
                    "libglib2.0-0t64"),
    "libpango-1.0.so.0": "libpango-1.0-0", "libpangocairo-1.0.so.0": "libpangocairo-1.0-0",
    "libsystemd.so.0": "libsystemd0", "libxcb.so.1": "libxcb1",
    "libxcb-dri3.so.0": "libxcb-dri3-0", "libz.so.1": "zlib1g",
}
HOOK_DEPENDENCIES = {
    "bash", "coreutils", "findutils", "grep", "sed", "gawk", "dpkg",
    "libc-bin", "procps", "psmisc", "iptables", "iproute2", "sudo",
    "systemd", "systemd-sysv", "libpam-systemd", "xdg-utils",
}


class AdaptError(Exception):
    """An unsupported input or failed validation."""


def run(*args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={**os.environ, "LC_ALL": "C"})
    if result.returncode:
        raise AdaptError(f"命令失败：{shlex.join(map(str, args))}\n{result.stderr.strip()}")
    return result.stdout


def regular_file(root, relative):
    path = root / relative
    # Never follow an archive-provided symlink while modifying the package.
    if not path.is_file() or path.is_symlink() or root.resolve() not in path.resolve().parents:
        raise AdaptError(f"安装包结构不受支持，缺少普通文件：{relative}")
    return path


def replace_exact(text, old, new, count=1):
    if text.count(old) != count:
        raise AdaptError(f"补丁匹配失败（预期 {count} 处）：{old[:100]!r}")
    return text.replace(old, new)


def replace_function(text, name, body):
    # Supported vendor functions have unindented declarations and closing braces.
    pattern = rf"(?m)^(?:function )?{re.escape(name)}\(\) *\{{\n.*?^\}}"
    matches = list(re.finditer(pattern, text, flags=re.S))
    if len(matches) != 1:
        raise AdaptError(f"函数补丁匹配失败：{name}")
    match = matches[0]
    return text[:match.start()] + f"{name}() {{\n{body}\n}}" + text[match.end():]


def validate_identity(fields):
    expected = {"Package": PACKAGE, "Version": VERSION, "Architecture": "amd64"}
    for key, value in expected.items():
        if fields.get(key) != value:
            raise AdaptError(f"不支持的 {key}：{fields.get(key)!r}，仅支持 {value}")


def control_fields(text):
    return dict(re.findall(r"^([A-Za-z][A-Za-z0-9-]*): *(.*)$", text, flags=re.M))


def patch_scripts(root, original_name):
    common = regular_file(root, "DEBIAN/common")
    common.write_text(replace_exact(common.read_text(), "#!/bin/bash\n",
                                   "#!/bin/bash\n\n" + ENVIRONMENT), encoding="utf-8")
    detect = '''  RELEASE_ID=$( . /etc/os-release; printf '%s' "${ID:-unknown}")
  SYSTEM_TYPE=${RELEASE_ID}
  RELEASE_TYPE=${RELEASE_ID}
  SYSTEM_VERSION_ID=$( . /etc/os-release; printf '%s' "${VERSION_ID:-}")
  VERSION_ID=${SYSTEM_VERSION_ID}'''
    for relative in (Path("DEBIAN/preinst"), Path("DEBIAN/postinst"),
                     Path("DEBIAN/prerm"), SHELL / "aTrustTrayStart.sh"):
        path = regular_file(root, relative)
        text = replace_function(path.read_text(), "detect_distro", detect)
        # Also replace queries outside detect_distro (preinst/prerm and tray).
        text = text.replace('$(lsb_release -i | cut -f2)',
                            '$( . /etc/os-release; printf \'%s\' "${ID:-unknown}")')
        text = text.replace('$(lsb_release -r | cut -f2)',
                            '$( . /etc/os-release; printf \'%s\' "${VERSION_ID:-}")')
        text = text.replace("#系统判断,lsb_release适应所有linux系统", "# 从 os-release 识别系统")
        if "lsb_release" in text:
            raise AdaptError(f"仍有未适配的发行版识别：{relative}")
        if relative == Path("DEBIAN/preinst"):
            text = replace_exact(text, 'PAK_ARCH="REPLACE_ARCH"', 'PAK_ARCH="amd64"')
            guard = '''# Reject unsupported installation targets before vendor side effects.
case "${1:-}" in
  install|upgrade)
    if ! ( . /etc/os-release; [ "${ID:-}" = debian ] && [ "${VERSION_ID:-}" = 13 ] ); then
      echo "此适配包仅支持 Debian 13 amd64。" >&2
      exit 1
    fi
    ;;
esac
'''
            text = replace_exact(text, "#!/bin/bash\n", "#!/bin/bash\n\n" + guard)
            text = replace_exact(text,
                                 "export QT_QPA_PLATFORM_PLUGIN_PATH=/usr/share/sangfor/aTrust/resources/lib",
                                 f"export QT_QPA_PLATFORM_PLUGIN_PATH={PLUGIN_DIR}/platforms")
        elif relative == Path("DEBIAN/postinst"):
            text = replace_function(text, "get_package_file_name",
                                    "  # Preserve the original vendor filename for SPA/address parsing.\n"
                                    f"  package_file_name={shlex.quote(original_name)}")
            text = replace_function(text, "delete_std_c6",
                                    "  # Bundled C++ runtimes were removed while repacking.\n  return 0")
            text = replace_function(text, "modify_desktop",
                                    "  # Desktop entries already use the Debian environment wrapper.\n  return 0")
        elif relative == SHELL / "aTrustTrayStart.sh":
            text = replace_exact(text, "#!/bin/bash\n", "#!/bin/bash\n\n" + ENV_SOURCE)
            text = replace_exact(text, "SYSTEM_VERSION_ID=''=''", "SYSTEM_VERSION_ID=''")
        path.write_text(text, encoding="utf-8")

    daemon = regular_file(root, SHELL / "aTrustDaemon.sh")
    daemon.write_text(replace_exact(daemon.read_text(), "#!/bin/bash\n",
                                    "#!/bin/bash\n\n" + ENV_SOURCE), encoding="utf-8")
    # The vendor typo otherwise emits a command-not-found error on every install.
    create_dir = regular_file(root, "DEBIAN/create_dir")
    create_dir.write_text(replace_exact(create_dir.read_text(), '    locat var_dir=""',
                                        '    local var_dir=""'), encoding="utf-8")
    for name, text in {
        "aTrustDebianEnv.sh": "#!/bin/bash\n" + ENVIRONMENT,
        "aTrustDebianExec.sh": "#!/bin/bash\nset -e\n" + ENV_SOURCE + 'exec "$@"\n',
    }.items():
        path = root / SHELL / name
        if path.exists() or path.is_symlink():
            raise AdaptError(f"安装包已有适配文件：{path.name}")
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
    for relative in DESKTOPS:
        path = regular_file(root, relative)
        text = replace_exact(path.read_text(),
                             "Exec=/usr/share/sangfor/aTrust/aTrustTray --no-sandbox --disable-gpu",
                             "Exec=/usr/share/sangfor/aTrust/resources/shell/aTrustDebianExec.sh "
                             "/usr/share/sangfor/aTrust/aTrustTray --no-sandbox --disable-gpu")
        if "StartupWMClass=" in text:
            raise AdaptError(f"桌面文件结构不受支持：{relative}")
        path.write_text(text.rstrip() + "\nStartupWMClass=aTrustTray\n", encoding="utf-8")


def remove_old_libraries(root):
    for name in REMOVE_LIBS:
        regular_file(root, RES / "lib" / name).unlink()
    regular_file(root, APP / "uem/lib/libstdc++.so.6").unlink()


def elf_inventory(root):
    inventory = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        output = run("readelf", "-dW", str(path))
        needed = set(re.findall(r"\(NEEDED\).*?\[(.*?)\]", output))
        sonames = set(re.findall(r"\(SONAME\).*?\[(.*?)\]", output))
        inventory[path] = (needed, sonames)
    return inventory


def dependencies(root):
    inventory = elf_inventory(root)
    provided = set()
    needed = set()
    for path, (names, sonames) in inventory.items():
        # Control archive executables aren't installed as payload libraries.
        if "DEBIAN" not in path.relative_to(root).parts:
            provided.add(path.name)
            provided.update(sonames)
        needed.update(names)
    for path in root.rglob("*"):
        if path.is_symlink() and path.exists() and root.resolve() in path.resolve().parents:
            provided.add(path.name)
    external = needed - provided
    unknown = external - SONAME_PACKAGES.keys()
    if unknown:
        raise AdaptError("发现未映射的外部运行库：" + ", ".join(sorted(unknown)))
    # Include X11Extras even if loaded dynamically, and SVG image support.
    return sorted(HOOK_DEPENDENCIES | {SONAME_PACKAGES[n] for n in external}
                  | {"libqt5x11extras5", "libqt5svg5"})


def update_metadata(root, depends):
    path = regular_file(root, "DEBIAN/control")
    text = replace_exact(path.read_text(), f"Version: {VERSION}\n",
                         f"Version: {ADAPTED_VERSION}\n")
    if "Depends:" in text or "Pre-Depends:" in text:
        raise AdaptError("安装包依赖字段已改变，拒绝覆盖")
    # Count payload storage, excluding package metadata; du handles directory allocation.
    size = run("du", "-sk", "--exclude=DEBIAN", str(root)).split()[0]
    text, count = re.subn(r"(?m)^Installed-Size: \d+$", f"Installed-Size: {size}", text)
    if count != 1:
        raise AdaptError("Installed-Size 字段不受支持")
    path.write_text(text.rstrip() + "\nDepends: " + ", ".join(depends) + "\n", encoding="utf-8")
    path.chmod(0o644)
    checksums = []
    for payload in sorted(root.rglob("*")):
        relative = payload.relative_to(root)
        if relative.parts[0] == "DEBIAN" or payload.is_symlink() or not payload.is_file():
            continue
        digest = hashlib.md5(payload.read_bytes()).hexdigest()
        checksums.append(f"{digest}  {relative.as_posix()}\n")
    (root / "DEBIAN/md5sums").write_text("".join(checksums), encoding="utf-8")


def validate_scripts(root):
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            first = stream.readline(128)
        if first.rstrip() == b"#!/bin/bash":
            run("bash", "-n", str(path))


def adapt(source, output):
    source = Path(source).absolute()
    output = Path(output).absolute()
    if not source.is_file():
        raise AdaptError(f"找不到输入包：{source}")
    if output.exists() or output.is_symlink():
        raise AdaptError(f"拒绝覆盖已有输出：{output}")
    if not output.parent.is_dir():
        raise AdaptError(f"输出目录不存在：{output.parent}")
    if any(char in source.name for char in "\n\r"):
        raise AdaptError("输入文件名不能包含换行符")
    for command in ("dpkg-deb", "readelf", "bash", "du"):
        if shutil.which(command) is None:
            raise AdaptError(f"缺少构建命令：{command}")
    validate_identity(control_fields(run("dpkg-deb", "-f", str(source))))
    # Work on the destination filesystem so publication is atomic and never overwrites.
    with tempfile.TemporaryDirectory(prefix=".atrust-repack-", dir=output.parent) as temporary:
        root = Path(temporary) / "package"
        print("解包并检查原始安装包……", flush=True)
        run("dpkg-deb", "--raw-extract", str(source), str(root))
        validate_identity(control_fields(regular_file(root, "DEBIAN/control").read_text()))
        patch_scripts(root, source.name)
        remove_old_libraries(root)
        depends = dependencies(root)
        update_metadata(root, depends)
        validate_scripts(root)
        print(f"补丁及脚本检查通过，声明 {len(depends)} 项依赖；正在打包……", flush=True)
        built = Path(temporary) / "adapted.deb"
        run("dpkg-deb", "--root-owner-group", "-Zxz", "-z6", "--build", str(root), str(built))
        fields = control_fields(run("dpkg-deb", "-f", str(built)))
        if (fields.get("Package"), fields.get("Version"), fields.get("Architecture")) != (
                PACKAGE, ADAPTED_VERSION, "amd64"):
            raise AdaptError("输出安装包元数据校验失败")
        # A hard link is an atomic exclusive publication, including against races.
        os.link(built, output)
    print(f"已生成：{output}\n版本：{ADAPTED_VERSION}\n未安装软件或修改系统配置。")


def main():
    parser = argparse.ArgumentParser(description="将 aTrust 2.5.16.50 amd64 deb 重打包为 Debian 13 适配包")
    parser.add_argument("input", nargs="?", default="aTrustInstaller_amd64.deb", help="原始 deb 文件")
    parser.add_argument("-o", "--output", default="aTrustInstaller_amd64_debian13.deb", help="输出 deb（拒绝覆盖）")
    args = parser.parse_args()
    try:
        adapt(args.input, args.output)
    except (AdaptError, OSError, UnicodeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消，临时目录已清理。", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
