"""운영 스크립트용 KIS 모의 클라이언트 생성 (키 주입 경로 3가지, SECRETS.md 참고)."""

from __future__ import annotations

import getpass
import os

from ..broker.kis import KisPaperClient, KisPaperCredentials
from ..config import Secret


def add_key_args(ap) -> None:
    ap.add_argument("--prompt-keys", action="store_true",
                    help="앱키·시크릿을 터미널에서 직접 입력(화면 표시 안 됨, 디스크 저장 안 함)")
    ap.add_argument("--secrets-file", help="비밀값 파일 경로 (기본 ~/.config/stockking/secrets.env, chmod 600)")


def make_client(args) -> KisPaperClient:
    if args.secrets_file:
        os.environ["STOCKKING_SECRETS_FILE"] = args.secrets_file
    if args.prompt_keys:
        creds = KisPaperCredentials(
            app_key=Secret(getpass.getpass("KIS 모의 APP KEY: ").strip()),
            app_secret=Secret(getpass.getpass("KIS 모의 APP SECRET: ").strip()),
            account_no=input("모의 계좌번호 앞 8자리: ").strip(),
            account_product=(input("상품코드 2자리 [01]: ").strip() or "01"),
        )
    else:
        creds = KisPaperCredentials.from_env()
    return KisPaperClient(creds)
