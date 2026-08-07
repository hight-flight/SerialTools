#!/usr/bin/env python3
"""Ubuntu 20.04 兼容发布包的独立底层构建入口。"""

from pathlib import Path

from build_linux import PROJECT_ROOT, main


UBUNTU_BASELINE = "20.04"
GLIBC_BASELINE = "2.31"


if __name__ == "__main__":
    raise SystemExit(
        main(
            ubuntu_baseline=UBUNTU_BASELINE,
            glibc_baseline=GLIBC_BASELINE,
            build_dir_name="linux20",
            default_output_dir=Path(PROJECT_ROOT) / "dist" / "linux20",
            required_ubuntu=UBUNTU_BASELINE,
        )
    )
