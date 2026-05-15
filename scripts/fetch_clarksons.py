#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clarksons 신조선가 지수 fetcher
================================
- 한국투자증권 강경태 텔레그램 채널(enc210406)에서 매주 신조선가 메시지 수집
- AWAKE 프로젝트의 기존 세션(awake_session.session)을 재활용 → 별도 인증 불필요

산출물:
- /tmp/clarksons_latest.json  (1차원 list, 각 항목: {"date": "YYYY-MM-DD", "value": float, "msg_id": int})

사용법:
    python3 fetch_clarksons.py              # 최근 60일 메시지 스캔
    python3 fetch_clarksons.py --days 180   # 더 길게

스케줄 호출 예:
    cp "/Users/songsangho/Desktop/Claude/AWAKE 전자 공시/awake_session.session" /tmp/awake_session.session
    python3 ".../scripts/fetch_clarksons.py"
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone, timedelta

from telethon.sync import TelegramClient

# ============= 설정 =============
API_ID = 37363162
API_HASH = "30cd2adb1edfe44f20929ff77bc87053"
CHANNEL = "enc210406"

# 세션 파일 후보 위치 (호스트 macOS / 샌드박스 Linux 양쪽 지원)
# AWAKE 프로젝트의 awake_session 을 그대로 재활용.
import glob as _glob

SESSION_SRC_CANDIDATES = [
    "/Users/songsangho/Desktop/투자 캘린더 Tool/awake_session.session",         # 투자 캘린더 Tool 폴더 (Mac 호스트 경로)
    "/Users/songsangho/Desktop/Claude/AWAKE 전자 공시/awake_session.session",  # AWAKE 원본 (Mac 호스트 경로)
    "/sessions/clever-zealous-ramanujan/mnt/AWAKE 전자 공시/awake_session.session",  # 샌드박스 (레거시)
]
# 샌드박스 환경에서 마운트된 경로 자동 탐색 (glob)
for p in _glob.glob("/sessions/*/mnt/투자 캘린더 Tool/awake_session.session"):
    SESSION_SRC_CANDIDATES.insert(0, p)  # 최우선으로 앞에 추가
# bash glob 으로도 한 번 더 fallback
for p in _glob.glob("/sessions/*/mnt/AWAKE*/awake_session.session"):
    SESSION_SRC_CANDIDATES.append(p)

# /tmp 에 다른 사용자 소유 파일이 있을 수 있어 충돌 회피용 prefix 사용
SESSION_DST = "/tmp/clarksons_session.session"
SESSION_PATH_NO_EXT = "/tmp/clarksons_session"  # Telethon은 확장자 없이 받음

OUT_PATH = "/tmp/clarksons_latest.json"

KST = timezone(timedelta(hours=9))

# 신조선가 메시지 정규식
# 예) "2026년 4월 24일 기준 클락슨 신조선가 지수는 183.41pt로..."
# 일부 메시지는 "신조선가지수" 또는 "신조선가 지수"의 띄어쓰기가 다를 수 있어 유연하게.
DATE_VALUE_RE = re.compile(
    r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일.*?신조선가\s*지수.*?(\d{2,4}\.\d{1,3})\s*p?t?",
    re.DOTALL,
)
KEYWORD_RE = re.compile(r"(클락슨|신조선가)")


def ensure_session():
    """후보 경로 중 첫 번째 존재 파일을 /tmp 로 복사."""
    src = None
    for cand in SESSION_SRC_CANDIDATES:
        if os.path.exists(cand):
            src = cand
            break
    if src is None:
        print("❌ 세션 파일 후보를 모두 찾지 못했습니다:", file=sys.stderr)
        for c in SESSION_SRC_CANDIDATES:
            print(f"   - {c}", file=sys.stderr)
        sys.exit(1)
    shutil.copy2(src, SESSION_DST)
    print(f"  세션 복사: {src} → {SESSION_DST}")
    return SESSION_PATH_NO_EXT


def parse_message(text: str):
    """메시지 본문에서 신조선가 (날짜, 값) 페어 추출. 없으면 None."""
    if not text:
        return None
    if not KEYWORD_RE.search(text):
        return None
    m = DATE_VALUE_RE.search(text)
    if not m:
        return None
    y, mo, d, val = m.groups()
    try:
        date_iso = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        value = float(val)
        # 신조선가 인덱스는 보통 100~250 범위. 그 밖이면 잘못 잡힌 숫자일 가능성.
        if not (50 <= value <= 500):
            return None
        return date_iso, value
    except Exception:
        return None


def fetch(days_back: int = 60):
    session_path = ensure_session()
    cutoff_kst = datetime.now(KST) - timedelta(days=days_back)

    print(f"▶ 채널 @{CHANNEL} 스캔 (최근 {days_back}일)...")

    rows = []
    seen_dates = set()
    debug_keyword_hits = 0

    client = TelegramClient(session_path, API_ID, API_HASH)
    client.connect()

    if not client.is_user_authorized():
        print("❌ 세션이 인증되지 않았습니다. AWAKE 프로젝트에서 재인증 필요.", file=sys.stderr)
        client.disconnect()
        sys.exit(2)

    try:
        for msg in client.iter_messages(CHANNEL, limit=None):
            if msg.date is None:
                continue
            kst_dt = msg.date.astimezone(KST)
            if kst_dt < cutoff_kst:
                break
            text = msg.text or ""
            if KEYWORD_RE.search(text):
                debug_keyword_hits += 1
            parsed = parse_message(text)
            if parsed is None:
                continue
            date_iso, value = parsed
            if date_iso in seen_dates:
                continue
            seen_dates.add(date_iso)
            rows.append({
                "date": date_iso,
                "value": value,
                "msg_id": msg.id,
                "msg_date_kst": kst_dt.strftime("%Y-%m-%d %H:%M"),
            })
    finally:
        client.disconnect()

    rows.sort(key=lambda r: r["date"])

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    print(f"✓ 키워드 hit: {debug_keyword_hits}개, 파싱 성공: {len(rows)}개")
    print(f"✓ 저장: {OUT_PATH}")
    if rows:
        print(f"  최근 5건:")
        for r in rows[-5:]:
            print(f"    {r['date']}  {r['value']:.2f}pt  (msg_id={r['msg_id']}, posted {r['msg_date_kst']})")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="과거 며칠 치 메시지를 스캔할지")
    args = ap.parse_args()
    rows = fetch(days_back=args.days)
    return 0 if rows else 3  # 0건이면 종료코드 3


if __name__ == "__main__":
    sys.exit(main())
