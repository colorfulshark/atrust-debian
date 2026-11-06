# aTrust Debian 13 适配脚本

将本目录的 Ubuntu 版 `aTrustInstaller_amd64.deb` 重打包为 Debian 13 amd64 安装包。
仅支持包名 `cn.com.sangfor.atrust`、版本 `2.5.16.50`、架构 `amd64`；输出版本为
`2.5.16.50+debian13.1`。不支持将已经适配的包再次作为输入。

脚本只生成 deb，无需 root，不安装依赖、不执行原厂安装钩子、不启动客户端或服务。
脚本本身不联网，也不会修改 `/etc/os-release` 或软件源。

## 生成安装包

构建需要 Python 3、`dpkg-deb`、`readelf`（binutils）、Bash 和 GNU `du`（coreutils）。
Debian 13 通常只需额外准备 binutils。临时目录放在输出文件的同一目录，建议该磁盘至少有
2 GB 可用空间；压缩可能耗时数分钟。

```bash
cd /home/ryan/atrust
python3 adapt_atrust_debian.py
```

默认输出 `aTrustInstaller_amd64_debian13.deb`。指定输入和输出：

```bash
python3 adapt_atrust_debian.py '/路径/aTrustInstaller_amd64.deb' -o '/路径/适配包.deb'
```

输入文件保留不变。已有输出文件（包括符号链接）会导致脚本报错；再次构建请用 `-o` 指定新文件。
异常或取消时会清理临时目录，只有打包及校验成功后才发布输出文件。
包名、版本、架构、补丁位置、库文件列表或外部依赖不符合预期时，脚本会拒绝生成新包。

请使用原始下载文件名作为输入：原厂通过文件名解析服务器地址、主题和 SPA 信息。
脚本会把输入文件的原始名称安全地保存在安装钩子中，因此生成包之后可以改名，
也支持 APT 配置或重试时无法获得原始 deb 路径的情况。

## 适配内容

- 使用 Debian 13 的 Qt 5、Qt 平台插件、XCB 和 C++ 运行库，删除包内对应旧库。
- 删除没有其他保留组件引用的旧 LDAP／Heimdal 和旧 Qt 配套库，消除对
  `libgcrypt.so.11`、`libgnutls.so.26` 的残留依赖。
- 保留 aTrust 专用库、Boost、原厂 OpenSSL 1.1、Electron 等其他客户端组件。
- 扫描全部 ELF 文件的依赖，映射为 Debian 13 软件包，补齐安装／卸载脚本依赖。
- 使用 `/etc/os-release` 识别 Debian，通过 systemd 管理服务；安装前拒绝 Debian 12、Ubuntu 等其他目标。
- 桌面入口、托盘脚本及守护进程统一使用客户端专用环境设置，不写入全局库配置。
- 保留原厂认证、配置迁移、服务和卸载逻辑；修正原脚本的 `locat` 拼写错误。
- 重新生成 `Installed-Size` 和 `md5sums`，将 deb 内的文件归属设置为 root。

Qt 使用系统插件目录 `/usr/lib/x86_64-linux-gnu/qt5/plugins`。
图形客户端沿用原厂 X11 启动方式，Wayland 桌面需要可用的 XWayland。
软件源中应包含 Debian 13 主仓库及安全更新仓库。

## 手动安装

先查看依赖解析结果：

```bash
apt-get -s install ./aTrustInstaller_amd64_debian13.deb
```

需要部署时，由用户执行：

```bash
sudo apt install ./aTrustInstaller_amd64_debian13.deb
```

实际安装会执行原厂钩子、修改客户端配置并启用／启动服务。建议通过 APT 安装以处理依赖。
安装后可以从应用菜单启动 aTrust，检查服务和日志：

```bash
systemctl status aTrustDaemon
journalctl -u aTrustDaemon -b
sudo less /root/.aTrust/logs/aTrustInstallOut.log
```

企业登录、SPA 认证、隧道连通、DNS、托盘显示、桌面重登录及升级／卸载行为，
需要在有实际服务端和桌面会话的 Debian 13 设备上验收。
本项目的重打包和依赖检查不代表这些业务功能已经通过测试。

## 测试

快速检查参数、覆盖保护、错误架构／版本以及不支持的包结构：

```bash
python3 -m unittest discover -s tests -v
```

使用真实包运行全部集成测试（会重打包，并在原包所在磁盘创建临时目录，建议预留 3 GB）：

```bash
ATRUST_TEST_DEB="$PWD/aTrustInstaller_amd64.deb" python3 -m unittest discover -s tests -v
```

集成测试验证原包 SHA-256 不变、未修改载荷的内容／权限／符号链接不变、
生成包的元数据和校验和、所有 Bash 脚本语法、特殊文件名不会执行 shell 命令、
Debian 13 使用 systemd，以及其他发行版在执行原厂操作前被拒绝。

额外提供 `tests/verify_runtime.py`，在 Debian 13 amd64 上对解包目录进行 Qt 符号比较及
动态加载器 `--list` 检查。第三个参数是将缺少的 Debian 依赖包下载并解包后的目录；
已安装的系统库使用宿主机版本。工具不会启动客户端或安装任何软件：

```bash
python3 tests/verify_runtime.py /路径/原包解包目录 /路径/新包解包目录 /路径/系统依赖解包目录
```

## 本次验证记录

验证环境：Debian 13 amd64，系统 Qt 包版本 `5.15.15+dfsg-6+deb13u1`。

- 原始包 SHA-256：`88acaa4ba53917605a10a0568f24b876ca14a27821a6293b69f4f6aa97e5efda`。
- 输出包保留原包名，版本为 `2.5.16.50+debian13.1`，声明 58 项依赖。
- 14 项自动化检查通过；另已用包含空格、单引号和 shell 替换字符的输入名称完成真实重打包验证。
- APT 模拟安装成功，没有升级或卸载现有软件；没有执行实际安装。
- 16 个保留 ELF 文件使用的 662 个 Qt 符号均由系统 Qt 提供。
- 103 个 ELF（含系统 Qt XCB 插件）通过动态加载器依赖解析检查，没有执行客户端入口。
- 企业服务端登录、图形桌面显示和隧道连通尚未进行实际验收。
