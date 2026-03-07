"""
디버그 스크립트 - GUI 없이 단계별 크롤링 결과 출력
사용: python debug_crawl.py
"""

import sys
import json
from crawler import GnuBoardCrawler


BASE_URL = "https://www.bufs.ac.kr"

# 사용자가 제공한 샘플 URL
SAMPLE_BOARD    = "reg_board2"
SAMPLE_WR_ID    = 4
SAMPLE_DL_URL   = f"{BASE_URL}/bbs/download.php?bo_table=reg_board2&wr_id=4&no=17"


def log(msg):
    print(msg)


def progress(val):
    pass  # 디버그 모드에서는 진행률 생략


def sep(title=""):
    print(f"\n{'─'*60}")
    if title:
        print(f"  {title}")
        print(f"{'─'*60}")


crawler = GnuBoardCrawler(BASE_URL, "/tmp/bufs_debug", log, progress)

# ────────────────────────────────────────────────────────────────
# STEP 1: 메인 페이지에서 게시판 탐지
# ────────────────────────────────────────────────────────────────
sep("STEP 1: 게시판 탐지")
boards = crawler.find_boards()
print(json.dumps(boards, ensure_ascii=False, indent=2))

# ────────────────────────────────────────────────────────────────
# STEP 2: 샘플 게시판 게시글 목록 (1페이지만)
# ────────────────────────────────────────────────────────────────
sep("STEP 2: 게시글 목록 (reg_board2, 1페이지)")
url = f"{BASE_URL}/bbs/board.php?bo_table={SAMPLE_BOARD}"
resp = crawler.get(url)
if resp:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    ids = crawler._extract_wr_ids(soup, SAMPLE_BOARD)
    print(f"  wr_id 목록: {ids}")
    print(f"  다음 페이지 존재: {crawler._has_next_page(soup, 1)}")
    title = crawler._extract_board_title(soup)
    print(f"  게시판 제목: {title!r}")
else:
    print("  ❌ 응답 없음")

# ────────────────────────────────────────────────────────────────
# STEP 3: 샘플 게시글 첨부파일
# ────────────────────────────────────────────────────────────────
sep(f"STEP 3: 첨부파일 목록 (wr_id={SAMPLE_WR_ID})")
attachments = crawler.get_attachments(SAMPLE_BOARD, SAMPLE_WR_ID)
if attachments:
    for i, att in enumerate(attachments, 1):
        print(f"  [{i}] {att['filename']}")
        print(f"       → {att['url']}")
else:
    print("  첨부파일 없음 (또는 페이지 접근 실패)")

# ────────────────────────────────────────────────────────────────
# STEP 4: 샘플 파일 다운로드 테스트
# ────────────────────────────────────────────────────────────────
sep("STEP 4: 다운로드 테스트 (샘플 URL)")
ok = crawler.download_file(SAMPLE_DL_URL, "/tmp/bufs_debug/test", "sample_attachment")
print(f"  결과: {'✅ 성공' if ok else '❌ 실패'}")

import os
test_dir = "/tmp/bufs_debug/test"
if os.path.exists(test_dir):
    files = os.listdir(test_dir)
    print(f"  저장된 파일: {files}")
