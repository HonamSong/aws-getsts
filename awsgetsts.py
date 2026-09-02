#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""하위호환 엔트리 스크립트 / Backward-compatible entry script.

패키지 설치 시에는 ``awsgetsts`` 커맨드를 사용하세요.
When installed, prefer the ``awsgetsts`` console command instead.
"""

from __future__ import annotations

import sys

from awsgetsts.cli import main

if __name__ == "__main__":
    sys.exit(main())