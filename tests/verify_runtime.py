#!/usr/bin/env python3
"""Check Qt exports and loader dependency lists without starting aTrust.

Arguments are extracted original deb, extracted adapted deb, and a directory
containing extracted Debian dependency packages. Host Debian 13 libraries are
used for dependencies already installed. No install or service operation occurs.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adapt_atrust_debian as adapter


def symbols(path, defined):
    result = set()
    for line in adapter.run("readelf", "--dyn-syms", "-W", str(path)).splitlines():
        fields = line.split()
        if (len(fields) >= 8 and fields[0].endswith(":")
                and (fields[6] != "UND") == defined):
            result.add(fields[7].split("@")[0])
    return result


def verify(original, adapted, system):
    inventory = adapter.elf_inventory(adapted)
    old_exports, new_exports = set(), set()
    for old in (original / adapter.RES / "lib").glob("libQt5*.so.5"):
        replacement = system / "usr/lib/x86_64-linux-gnu" / old.name
        if old.name == "libQt5XcbQpa.so.5":
            # Debian packages this private library with its full version suffix.
            candidates = list(replacement.parent.glob("libQt5XcbQpa.so.5*"))
            if candidates:
                replacement = candidates[0]
        if not replacement.exists():
            raise adapter.AdaptError(f"系统库目录缺少 Qt 替代库：{old.name}")
        old_exports.update(symbols(old, True))
        new_exports.update(symbols(replacement, True))
    if not old_exports:
        raise adapter.AdaptError("原包目录缺少 Qt 库，无法进行 ABI 对比")
    users, required = 0, set()
    for path in inventory:
        used = symbols(path, False) & old_exports
        if used:
            users += 1
            required.update(used)
        missing = used - new_exports
        if missing:
            raise adapter.AdaptError(f"系统 Qt 缺少 {path.name} 使用的符号：{sorted(missing)}")
    print(f"Qt 导出符号检查通过：{users} 个 ELF，{len(required)} 个 Qt 符号。")

    directories = [adapted / adapter.APP, adapted / adapter.RES / "bin",
                   adapted / adapter.RES / "lib", adapted / adapter.APP / "uem/bin",
                   adapted / adapter.APP / "uem/lib", system / "usr/lib/x86_64-linux-gnu",
                   system / "lib/x86_64-linux-gnu"]
    environment = {k: v for k, v in os.environ.items() if not k.startswith("LD_")}
    environment["LC_ALL"] = "C"
    loader = "/lib64/ld-linux-x86-64.so.2"
    checked = set(inventory)
    # Include the system Qt platform plugin so its private ABI and XCB closure are checked.
    checked.update((system / "usr/lib/x86_64-linux-gnu/qt5/plugins/platforms").glob("libqxcb.so"))
    failures = []
    for path in sorted(checked):
        result = subprocess.run([loader, "--library-path", ":".join(map(str, directories)),
                                 "--list", str(path)], env=environment,
                                capture_output=True, text=True)
        if result.returncode or "not found" in result.stdout + result.stderr:
            failures.append(f"{path}:\n{result.stdout}{result.stderr}")
    if failures:
        raise adapter.AdaptError("动态库解析失败：\n" + "\n".join(failures))
    print(f"动态加载器 --list 检查通过：{len(checked)} 个 ELF；未执行客户端入口。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original_root", type=Path)
    parser.add_argument("adapted_root", type=Path)
    parser.add_argument("system_root", type=Path)
    args = parser.parse_args()
    try:
        verify(args.original_root.resolve(), args.adapted_root.resolve(), args.system_root.resolve())
    except (adapter.AdaptError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
