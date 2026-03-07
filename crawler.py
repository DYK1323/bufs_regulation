"""
웹사이트 첨부파일 크롤러
- GUI로 URL 입력 및 저장 폴더 선택
- 게시판 목록 자동 탐지
- 모든 게시글의 첨부파일 다운로드
"""

import os
import re
import time
import threading
import urllib.parse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import requests
from bs4 import BeautifulSoup


# ────────────────────────────────────────────────────────────────
# 설정
# ────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
REQUEST_TIMEOUT = 30
DELAY_BETWEEN_REQUESTS = 0.5   # 서버 부하 방지 (초)
MAX_RETRIES = 3


# ────────────────────────────────────────────────────────────────
# 크롤러 핵심 로직
# ────────────────────────────────────────────────────────────────
class Crawler:
    def __init__(self, base_url: str, save_dir: str, log_callback, progress_callback):
        self.base_url = base_url.rstrip("/")
        self.save_dir = save_dir
        self.log = log_callback
        self.set_progress = progress_callback
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.stop_flag = False

        # 사이트 유형 자동 탐지에 사용할 게시판 URL 패턴
        self.board_patterns = [
            # GNUBOARD / 영카트
            r"/bbs/board\.php\?bo_table=\w+",
            # XpressEngine (XE)
            r"/index\.php\?mid=\w+",
            # 자체 CMS (예: /board/list, /board/view 등)
            r"/board/[^?#]+",
            r"/bbslist/[^?#]+",
            r"/cop/bbs/[^?#]+",
        ]

    # ── 공통 유틸 ────────────────────────────────────────────────
    def get(self, url: str, **kwargs) -> requests.Response | None:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                self.log(f"  [재시도 {attempt}/{MAX_RETRIES}] {url} → {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(2 ** attempt)
        return None

    def safe_filename(self, name: str) -> str:
        """파일 이름에서 OS 금지 문자 제거"""
        return re.sub(r'[\\/:*?"<>|]', "_", name).strip()

    def make_absolute(self, href: str) -> str:
        return urllib.parse.urljoin(self.base_url, href)

    # ── 1단계: 메인 페이지에서 게시판 목록 탐지 ─────────────────
    def find_boards(self) -> list[dict]:
        """메인 페이지 + 상단 메뉴에서 게시판 URL을 수집"""
        self.log(f"[1단계] 메인 페이지 접속: {self.base_url}")
        resp = self.get(self.base_url)
        if not resp:
            self.log("  ❌ 메인 페이지 접속 실패")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        boards = {}

        # <a> 태그 전체 스캔
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            abs_url = self.make_absolute(href)
            # 같은 도메인인지 확인
            if urllib.parse.urlparse(abs_url).netloc != urllib.parse.urlparse(self.base_url).netloc:
                continue
            for pat in self.board_patterns:
                if re.search(pat, abs_url):
                    # 게시판 목록 URL만 (view 페이지 제외)
                    if not re.search(r"(wr_id|view|read|detail|content)=", abs_url):
                        label = a.get_text(strip=True) or abs_url
                        if abs_url not in boards:
                            boards[abs_url] = label
                        break

        # 탐지 실패 시 regulation.bufs.ac.kr 전용 경험적 패턴 시도
        if not boards:
            boards = self._find_boards_fallback(soup)

        result = [{"url": url, "name": name} for url, name in boards.items()]
        self.log(f"  → 게시판 {len(result)}개 발견")
        for b in result:
            self.log(f"     • {b['name']}  ({b['url']})")
        return result

    def _find_boards_fallback(self, soup: BeautifulSoup) -> dict:
        """
        일반 패턴으로 탐지 실패 시 사용하는 보완 탐지:
        네비게이션 메뉴, 사이드바 등에서 /로 시작하는 링크 중
        '규정', '규칙', '지침', '규약' 등의 키워드가 포함된 것을 수집
        """
        self.log("  [보완 탐지] 키워드 기반 게시판 탐색 중...")
        boards = {}
        keywords = ["규정", "규칙", "지침", "훈령", "예규", "고시", "공고", "운영", "학칙"]
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            abs_url = self.make_absolute(href)
            if urllib.parse.urlparse(abs_url).netloc != urllib.parse.urlparse(self.base_url).netloc:
                continue
            text = a.get_text(strip=True)
            if any(kw in text for kw in keywords) and len(text) < 30:
                if not re.search(r"(wr_id|view|read|detail|content|javascript)", abs_url):
                    boards[abs_url] = text
        return boards

    # ── 2단계: 게시판에서 게시글 목록 수집 ──────────────────────
    def get_post_urls(self, board_url: str, board_name: str) -> list[str]:
        """게시판의 모든 페이지를 순회하며 게시글 URL 수집"""
        post_urls = []
        page_url = board_url
        visited_pages = set()
        page_num = 1

        while page_url and not self.stop_flag:
            if page_url in visited_pages:
                break
            visited_pages.add(page_url)

            self.log(f"  [게시글 목록] {board_name} - {page_num}페이지")
            resp = self.get(page_url)
            if not resp:
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            found = self._extract_post_urls(soup, board_url)
            post_urls.extend(found)

            next_url = self._find_next_page(soup, page_url, page_num)
            page_url = next_url
            page_num += 1
            time.sleep(DELAY_BETWEEN_REQUESTS)

        return list(dict.fromkeys(post_urls))  # 중복 제거

    def _extract_post_urls(self, soup: BeautifulSoup, board_url: str) -> list[str]:
        """게시글 상세 페이지 URL 추출"""
        urls = []
        parsed_board = urllib.parse.urlparse(board_url)

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            abs_url = self.make_absolute(href)
            parsed = urllib.parse.urlparse(abs_url)

            if parsed.netloc != parsed_board.netloc:
                continue

            qs = urllib.parse.parse_qs(parsed.query)

            # GNUBOARD: wr_id 파라미터
            if "wr_id" in qs:
                urls.append(abs_url)
            # XE: document_srl 파라미터
            elif "document_srl" in qs:
                urls.append(abs_url)
            # 경로 기반 상세 페이지: /board/view/123, /bbs/read/123 등
            elif re.search(r"/(view|read|detail|content|post)/\d+", parsed.path):
                urls.append(abs_url)
            # 숫자 ID가 경로 마지막에 오는 경우
            elif re.search(r"/\d+$", parsed.path) and parsed.path != parsed_board.path:
                urls.append(abs_url)

        return urls

    def _find_next_page(self, soup: BeautifulSoup, current_url: str, current_page: int) -> str | None:
        """다음 페이지 URL 탐지"""
        parsed = urllib.parse.urlparse(current_url)
        qs = urllib.parse.parse_qs(parsed.query)

        # 페이지네이션 링크 탐색
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"].strip()

            # "다음", ">" 등의 텍스트로 다음 페이지 링크 탐지
            if text in ("다음", "다음페이지", "next", ">", "»"):
                abs_url = self.make_absolute(href)
                if abs_url != current_url:
                    return abs_url

        # 쿼리 파라미터 방식 페이지 증가 시도 (page, pg, p, offset 등)
        for param in ("page", "pg", "p", "pageIndex", "pageNo", "cur_page"):
            if param in qs:
                next_qs = qs.copy()
                next_val = int(qs[param][0]) + 1
                next_qs[param] = [str(next_val)]
                new_query = urllib.parse.urlencode(next_qs, doseq=True)
                next_url = parsed._replace(query=new_query).geturl()
                # 다음 페이지가 현재 페이지와 실제로 다른지 확인
                resp = self.get(next_url)
                if resp and resp.url != current_url:
                    # 게시글이 실제로 있는지 확인
                    test_soup = BeautifulSoup(resp.text, "html.parser")
                    if self._extract_post_urls(test_soup, current_url):
                        return next_url
                return None

        return None

    # ── 3단계: 게시글에서 첨부파일 URL 수집 ─────────────────────
    def get_attachments(self, post_url: str) -> list[dict]:
        """게시글 페이지에서 첨부파일 링크 추출"""
        resp = self.get(post_url)
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        attachments = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            abs_url = self.make_absolute(href)

            if abs_url in seen:
                continue

            # 첨부파일 판별 기준
            is_attachment = (
                # 파일 다운로드 URL 패턴
                re.search(r"/(download|attach|file|atch|filedown|dext5down|dn)\b", abs_url, re.I)
                or re.search(r"\.(hwp|hwpx|pdf|doc|docx|xls|xlsx|ppt|pptx|zip|alz|egg|7z|txt)(\?|$)", abs_url, re.I)
                or re.search(r"(fileId|attachId|file_id|atch_file_id|seq)=", abs_url)
            )

            if is_attachment:
                filename = self._guess_filename(a, abs_url)
                attachments.append({"url": abs_url, "filename": filename})
                seen.add(abs_url)

        return attachments

    def _guess_filename(self, a_tag, url: str) -> str:
        """첨부파일 이름 추정"""
        # 링크 텍스트 우선
        text = a_tag.get_text(strip=True)
        if text and not text.startswith("http") and len(text) < 200:
            # 이미 확장자가 있으면 그대로 사용
            if re.search(r"\.\w{2,5}$", text):
                return self.safe_filename(text)
            # 링크 텍스트 + URL에서 확장자 보완
            ext_match = re.search(r"\.(\w{2,5})(\?|$)", url)
            if ext_match:
                return self.safe_filename(f"{text}.{ext_match.group(1)}")
            return self.safe_filename(text)

        # URL 마지막 경로 세그먼트
        path = urllib.parse.urlparse(url).path
        filename = path.split("/")[-1]
        if filename:
            return self.safe_filename(urllib.parse.unquote(filename))

        return "attachment"

    # ── 4단계: 파일 다운로드 ─────────────────────────────────────
    def download_file(self, url: str, dest_dir: str, filename: str) -> bool:
        os.makedirs(dest_dir, exist_ok=True)
        filepath = os.path.join(dest_dir, filename)

        # 이름 충돌 처리
        base, ext = os.path.splitext(filepath)
        counter = 1
        while os.path.exists(filepath):
            filepath = f"{base}_{counter}{ext}"
            counter += 1

        try:
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT, stream=True)
            resp.raise_for_status()

            # Content-Disposition에서 파일명 재추출 시도
            cd = resp.headers.get("Content-Disposition", "")
            cd_match = re.search(r'filename[*]?=["\']?([^"\';\r\n]+)', cd)
            if cd_match:
                raw_name = cd_match.group(1).strip()
                # RFC 5987 인코딩 처리
                if raw_name.startswith("UTF-8''"):
                    raw_name = urllib.parse.unquote(raw_name[7:])
                else:
                    try:
                        raw_name = raw_name.encode("latin-1").decode("utf-8")
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        pass
                new_name = self.safe_filename(raw_name)
                if new_name:
                    filepath = os.path.join(dest_dir, new_name)
                    base, ext = os.path.splitext(filepath)
                    counter = 1
                    while os.path.exists(filepath):
                        filepath = f"{base}_{counter}{ext}"
                        counter += 1

            with open(filepath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            size = os.path.getsize(filepath)
            self.log(f"    ✅ 저장: {os.path.basename(filepath)}  ({size:,} bytes)")
            return True

        except Exception as e:
            self.log(f"    ❌ 다운로드 실패: {url} → {e}")
            return False

    # ── 메인 실행 ────────────────────────────────────────────────
    def run(self):
        self.stop_flag = False
        total_files = 0

        boards = self.find_boards()
        if not boards:
            self.log("\n게시판을 찾지 못했습니다. URL을 확인해 주세요.")
            return

        for b_idx, board in enumerate(boards):
            if self.stop_flag:
                break

            board_name = self.safe_filename(board["name"]) or f"board_{b_idx+1}"
            board_dir = os.path.join(self.save_dir, board_name)
            self.log(f"\n{'='*60}")
            self.log(f"[게시판] {board['name']}")

            post_urls = self.get_post_urls(board["url"], board["name"])
            self.log(f"  → 게시글 {len(post_urls)}개")

            for p_idx, post_url in enumerate(post_urls):
                if self.stop_flag:
                    break

                self.log(f"\n  [게시글 {p_idx+1}/{len(post_urls)}] {post_url}")
                attachments = self.get_attachments(post_url)

                if not attachments:
                    self.log("    첨부파일 없음")
                else:
                    self.log(f"    첨부파일 {len(attachments)}개")
                    post_dir = os.path.join(board_dir, f"post_{p_idx+1:04d}")
                    for att in attachments:
                        if self.stop_flag:
                            break
                        ok = self.download_file(att["url"], post_dir, att["filename"])
                        if ok:
                            total_files += 1

                # 진행률 업데이트
                progress = ((b_idx * len(post_urls) + p_idx + 1) /
                            max(len(boards) * len(post_urls), 1)) * 100
                self.set_progress(min(progress, 100))
                time.sleep(DELAY_BETWEEN_REQUESTS)

        self.set_progress(100)
        self.log(f"\n{'='*60}")
        self.log(f"완료! 총 {total_files}개 파일 다운로드")
        self.log(f"저장 위치: {self.save_dir}")


# ────────────────────────────────────────────────────────────────
# GUI
# ────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("웹사이트 첨부파일 크롤러")
        self.resizable(True, True)
        self.minsize(720, 560)
        self._crawler: Crawler | None = None
        self._thread: threading.Thread | None = None
        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # ── 입력 영역 ──
        frame_top = ttk.LabelFrame(self, text="설정", padding=10)
        frame_top.pack(fill="x", **pad)
        frame_top.columnconfigure(1, weight=1)

        # URL
        ttk.Label(frame_top, text="웹사이트 URL:").grid(row=0, column=0, sticky="w", pady=4)
        self.url_var = tk.StringVar(value="https://regulation.bufs.ac.kr")
        ttk.Entry(frame_top, textvariable=self.url_var, width=60).grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=4
        )

        # 저장 폴더
        ttk.Label(frame_top, text="저장 폴더:").grid(row=1, column=0, sticky="w", pady=4)
        self.dir_var = tk.StringVar(value=os.path.expanduser("~/Downloads/crawled"))
        ttk.Entry(frame_top, textvariable=self.dir_var, width=50).grid(
            row=1, column=1, sticky="ew", padx=(6, 4), pady=4
        )
        ttk.Button(frame_top, text="찾아보기...", command=self._choose_dir).grid(
            row=1, column=2, sticky="e", pady=4
        )

        # ── 버튼 영역 ──
        frame_btn = ttk.Frame(self)
        frame_btn.pack(fill="x", padx=10, pady=(0, 4))

        self.btn_start = ttk.Button(frame_btn, text="▶  크롤링 시작", command=self._start)
        self.btn_start.pack(side="left", padx=(0, 8))

        self.btn_stop = ttk.Button(frame_btn, text="⏹  중지", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left")

        ttk.Button(frame_btn, text="로그 지우기", command=self._clear_log).pack(side="right")

        # ── 진행률 ──
        self.progress_var = tk.DoubleVar()
        self.progressbar = ttk.Progressbar(
            self, variable=self.progress_var, maximum=100, mode="determinate"
        )
        self.progressbar.pack(fill="x", padx=10, pady=(0, 4))

        self.progress_label = ttk.Label(self, text="대기 중", anchor="w")
        self.progress_label.pack(fill="x", padx=10)

        # ── 로그 영역 ──
        frame_log = ttk.LabelFrame(self, text="로그", padding=6)
        frame_log.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        self.log_box = scrolledtext.ScrolledText(
            frame_log, state="disabled", wrap="word",
            font=("Consolas", 9) if os.name == "nt" else ("Monospace", 9),
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white"
        )
        self.log_box.pack(fill="both", expand=True)

    # ── 이벤트 핸들러 ────────────────────────────────────────────
    def _choose_dir(self):
        d = filedialog.askdirectory(title="저장 폴더 선택", initialdir=self.dir_var.get())
        if d:
            self.dir_var.set(d)

    def _start(self):
        url = self.url_var.get().strip()
        save_dir = self.dir_var.get().strip()

        if not url:
            messagebox.showwarning("입력 오류", "URL을 입력하세요.")
            return
        if not save_dir:
            messagebox.showwarning("입력 오류", "저장 폴더를 선택하세요.")
            return

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.progress_var.set(0)
        self._append_log(f"크롤링 시작: {url}\n저장 위치: {save_dir}\n{'─'*60}\n")

        self._crawler = Crawler(
            base_url=url,
            save_dir=save_dir,
            log_callback=self._append_log,
            progress_callback=self._update_progress,
        )
        self._thread = threading.Thread(target=self._run_crawler, daemon=True)
        self._thread.start()

    def _stop(self):
        if self._crawler:
            self._crawler.stop_flag = True
            self.btn_stop.config(state="disabled")
            self._append_log("\n⏹ 중지 요청... 현재 작업 완료 후 종료됩니다.")

    def _run_crawler(self):
        try:
            self._crawler.run()
        except Exception as e:
            self._append_log(f"\n❌ 오류 발생: {e}")
        finally:
            self.after(0, self._on_done)

    def _on_done(self):
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    def _append_log(self, text: str):
        """스레드 안전 로그 추가"""
        def _do():
            self.log_box.config(state="normal")
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.after(0, _do)

    def _update_progress(self, value: float):
        def _do():
            self.progress_var.set(value)
            self.progress_label.config(text=f"진행률: {value:.1f}%")
        self.after(0, _do)

    def _clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")


# ────────────────────────────────────────────────────────────────
# 진입점
# ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
