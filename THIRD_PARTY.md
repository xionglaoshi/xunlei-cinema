# Third-party components

- [PanSou](https://github.com/fish2018/pansou), commit `1667c66f578a91144153f9dfba388d086c23449e`: the bundled macOS arm64 binary was built from its source. Its original MIT license is preserved at `vendor/PANSOU-LICENSE`. PanSou and its dependencies remain under their respective upstream licenses.
- [xunlei-cli](https://github.com/nesk-woe/xunlei-cli), commit `f6af5bfd6b2b182ed2129421285fc57e8db13706`: fetched separately by `scripts/setup.sh` into the ignored `vendor/xunlei-cli-src/` directory. No xunlei-cli source is included in this repository. As checked on 2026-09-28, upstream had no license file or GitHub-recognized license. This repository's MIT license does **not** cover xunlei-cli.

The skill's authored scripts and documentation are MIT licensed. The install script never copies an existing user's Xunlei token into the repository.
