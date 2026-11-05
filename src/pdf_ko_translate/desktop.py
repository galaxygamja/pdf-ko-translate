"""묶인 앱(PyInstaller)의 진입점.

같은 실행 파일이 두 가지로 쓰인다:
- 그냥 실행 → 앱 창
- `--run-cli ...` → 명령어 버전(번역 작업). 앱이 작업마다 자기 자신을 이렇게 다시 실행한다.
"""

from __future__ import annotations

import multiprocessing
import sys


def main() -> None:
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--run-cli":
        from .cli import main as cli_main

        sys.exit(cli_main(sys.argv[2:]))
    from .app import main as app_main

    sys.argv = sys.argv[:1]  # 맥이 붙이는 -psn_ 같은 인자는 무시
    app_main()


if __name__ == "__main__":
    main()
