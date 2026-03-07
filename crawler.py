"""
BUFS 규정집 첨부파일 크롤러 (GNUBOARD 전용)
대상: https://www.bufs.ac.kr (GNUBOARD 기반)
게시판 패턴: /bbs/board.php?bo_table=reg_boardN
다운로드 패턴: /bbs/download.php?bo_table=...&wr_id=...&no=...
"""

import os
import re
import time
import threading
import urllib.parse
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

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
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "",   # 요청마다 동적으로 채움
}
REQUEST_TIMEOUT = 30
DELAY_SECONDS = 0.5     # 서버 부하 방지
MAX_RETRIES = 3


# ────────────────────────────────────────────────────────────────
# 크롤러
# ────────────────────────────────────────────────────────────────
class GnuBoardCrawler:
    def __init__(self, base_url: str, save_dir: str, log_fn, progress_fn):
        self.base_url = base_url.rstrip("/")
        self.save_dir = save_dir
        self.log = log_fn
        self.set_progress = progress_fn
        self.stop_flag = False

        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # ── 공통 유틸 ────────────────────────────────────────────────

    def get(self, url: str, referer: str = "") -> requests.Response | None:
        headers = {"Referer": referer or self.base_url}
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                self.log(f"  [재시도 {attempt}/{MAX_RETRIES}] {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(2 ** attempt)
        self.log(f"  ❌ 요청 실패: {url}")
        return None

    def abs(self, href: str) -> str:
        return urllib.parse.urljoin(self.base_url, href)

    def safe_name(self, s: str) -> str:
        return re.sub(r'[\\/:*?"<>|]', "_", s).strip(" .")

    # ── 1단계: 게시판 목록 탐지 ──────────────────────────────────

    def find_boards(self) -> list[dict]:
        """
        메인 페이지 + 사이트 전체 탐색으로 reg_board* 테이블을 수집.
        못 찾으면 reg_board1~20 순차 시도.
        """
        self.log(f"[1단계] 게시판 탐색: {self.base_url}")
        boards = {}

        # 메인 페이지에서 링크 수집
        resp = self.get(self.base_url)
        if resp:
            boards.update(self._extract_board_links(resp.text))

        # 탐지 실패 시 순차 시도
        if not boards:
            self.log("  → 자동 탐지 실패. reg_board1~20 순차 확인 중...")
            boards = self._probe_boards(prefix="reg_board", count=20)

        result = [{"bo_table": k, "name": v, "url": self._board_url(k)}
                  for k, v in boards.items()]
        self.log(f"  → {len(result)}개 게시판 발견")
        for b in result:
            self.log(f"     • {b['name']}  (bo_table={b['bo_table']})")
        return result

    def _extract_board_links(self, html: str) -> dict:
        """HTML에서 bo_table=reg_board* 링크와 이름을 추출"""
        soup = BeautifulSoup(html, "html.parser")
        boards = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = re.search(r"bo_table=(reg_board\w+)", href)
            if m:
                bo_table = m.group(1)
                # wr_id가 없는 것(= 게시판 목록 링크)만
                if "wr_id" not in href and bo_table not in boards:
                    label = a.get_text(strip=True) or bo_table
                    boards[bo_table] = label
        return boards

    def _probe_boards(self, prefix: str, count: int) -> dict:
        """reg_board1 ~ reg_boardN 순차 시도"""
        boards = {}
        for i in range(1, count + 1):
            if self.stop_flag:
                break
            bo_table = f"{prefix}{i}"
            url = self._board_url(bo_table)
            resp = self.get(url)
            if not resp:
                continue
            # 빈 페이지 / 404 유사 응답 필터
            if "존재하지 않는" in resp.text or "없는 게시판" in resp.text:
                continue
            # 게시판 제목 추출
            soup = BeautifulSoup(resp.text, "html.parser")
            title = self._extract_board_title(soup) or bo_table
            boards[bo_table] = title
            self.log(f"    ✔ {bo_table}: {title}")
            time.sleep(DELAY_SECONDS)
        return boards

    def _board_url(self, bo_table: str, page: int = 1) -> str:
        url = f"{self.base_url}/bbs/board.php?bo_table={bo_table}"
        if page > 1:
            url += f"&page={page}"
        return url

    def _extract_board_title(self, soup: BeautifulSoup) -> str:
        """GNUBOARD 게시판 제목 추출"""
        for sel in ("#bo_wr_list_head h2", ".bo_tit", "h2.bo_tit", "#board h2", "h1"):
            tag = soup.select_one(sel)
            if tag:
                return tag.get_text(strip=True)
        return ""

    # ── 2단계: 게시글 목록 수집 ──────────────────────────────────

    def get_post_ids(self, bo_table: str, board_name: str) -> list[int]:
        """게시판의 모든 페이지를 순회하며 wr_id 수집"""
        self.log(f"\n[2단계] 게시글 목록 수집: {board_name}")
        wr_ids = []
        page = 1

        while not self.stop_flag:
            url = self._board_url(bo_table, page)
            resp = self.get(url, referer=self._board_url(bo_table, max(1, page - 1)))
            if not resp:
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            found = self._extract_wr_ids(soup, bo_table)

            if not found:
                self.log(f"  → {page}페이지: 게시글 없음 (종료)")
                break

            self.log(f"  → {page}페이지: {len(found)}개 게시글")
            wr_ids.extend(found)

            if not self._has_next_page(soup, page):
                break

            page += 1
            time.sleep(DELAY_SECONDS)

        # 중복 제거, 순서 유지
        seen = set()
        unique = []
        for wid in wr_ids:
            if wid not in seen:
                seen.add(wid)
                unique.append(wid)
        self.log(f"  → 총 {len(unique)}개 게시글 수집")
        return unique

    def _extract_wr_ids(self, soup: BeautifulSoup, bo_table: str) -> list[int]:
        """현재 페이지에서 wr_id 값 추출"""
        ids = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = re.search(rf"bo_table={re.escape(bo_table)}&wr_id=(\d+)", href)
            if m:
                ids.append(int(m.group(1)))
        return ids

    def _has_next_page(self, soup: BeautifulSoup, current_page: int) -> bool:
        """다음 페이지 존재 여부 확인"""
        # GNUBOARD 페이지네이션: .pg_wrap 또는 #pg_wrap 안에 페이지 링크
        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = re.search(r"page=(\d+)", href)
            if m and int(m.group(1)) == current_page + 1:
                return True
        # "다음" 텍스트 링크
        for a in soup.find_all("a"):
            text = a.get_text(strip=True)
            if text in ("다음", "다음페이지", "next", ">", "»"):
                return True
        return False

    # ── 3단계: 게시글에서 첨부파일 URL 수집 ─────────────────────

    def get_attachments(self, bo_table: str, wr_id: int) -> list[dict]:
        """게시글 상세 페이지에서 download.php 링크 추출"""
        post_url = (
            f"{self.base_url}/bbs/board.php"
            f"?bo_table={bo_table}&wr_id={wr_id}"
        )
        resp = self.get(post_url, referer=self._board_url(bo_table))
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # 게시글 제목 (폴더명용)
        title = self._extract_post_title(soup) or f"post_{wr_id}"

        attachments = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            abs_url = self.abs(href)

            # GNUBOARD 다운로드 링크 패턴
            if "/bbs/download.php" not in abs_url:
                continue
            if abs_url in seen:
                continue
            seen.add(abs_url)

            # no= 파라미터 확인
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(abs_url).query)
            if "no" not in qs:
                continue

            # 파일명 후보: 링크 텍스트 → qs → fallback
            filename = self._guess_attachment_name(a, qs)
            attachments.append({
                "url": abs_url,
                "filename": filename,
                "post_title": title,
            })

        return attachments

    def _extract_post_title(self, soup: BeautifulSoup) -> str:
        for sel in (".bo_v_tit h2", ".bo_v_con h2", "#bo_v_title", "h2.bo_v_tit", "h2"):
            tag = soup.select_one(sel)
            if tag:
                return self.safe_name(tag.get_text(strip=True))
        return ""

    def _guess_attachment_name(self, a_tag, qs: dict) -> str:
        # 링크 텍스트 우선
        text = a_tag.get_text(strip=True)
        if text and len(text) < 200 and "." in text:
            return self.safe_name(text)
        # qs에서 파일명 힌트가 있으면
        for key in ("file_name", "fname", "name"):
            if key in qs:
                return self.safe_name(urllib.parse.unquote(qs[key][0]))
        # no 번호를 파일명으로
        no = qs.get("no", ["0"])[0]
        return f"attachment_{no}"

    # ── 4단계: 파일 다운로드 ─────────────────────────────────────

    def download_file(self, url: str, dest_dir: str, hint_name: str) -> bool:
        os.makedirs(dest_dir, exist_ok=True)

        try:
            resp = self.session.get(
                url,
                headers={"Referer": self.base_url},
                timeout=REQUEST_TIMEOUT,
                stream=True,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            self.log(f"    ❌ 다운로드 실패: {e}")
            return False

        # Content-Disposition에서 실제 파일명 추출
        filename = self._parse_filename(resp.headers, hint_name)
        filepath = self._unique_path(dest_dir, filename)

        try:
            with open(filepath, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            size = os.path.getsize(filepath)
            self.log(f"    ✅ {os.path.basename(filepath)}  ({size:,} bytes)")
            return True
        except OSError as e:
            self.log(f"    ❌ 저장 실패: {e}")
            return False

    def _parse_filename(self, headers: dict, fallback: str) -> str:
        cd = headers.get("Content-Disposition", "")
        if not cd:
            return fallback

        # RFC 5987: filename*=UTF-8''...
        m = re.search(r"filename\*=UTF-8''(.+)", cd, re.I)
        if m:
            return self.safe_name(urllib.parse.unquote(m.group(1).strip()))

        # 일반 filename="..."
        m = re.search(r'filename=["\']?([^"\';\r\n]+)', cd, re.I)
        if m:
            raw = m.group(1).strip().strip('"\'')
            # EUC-KR / latin-1 → UTF-8 디코딩 시도
            for enc in ("utf-8", "euc-kr", "cp949"):
                try:
                    decoded = raw.encode("latin-1").decode(enc)
                    return self.safe_name(decoded)
                except (UnicodeDecodeError, UnicodeEncodeError):
                    continue
            return self.safe_name(raw)

        return fallback

    def _unique_path(self, directory: str, filename: str) -> str:
        base, ext = os.path.splitext(filename)
        path = os.path.join(directory, filename)
        counter = 1
        while os.path.exists(path):
            path = os.path.join(directory, f"{base}_{counter}{ext}")
            counter += 1
        return path

    # ── 메인 실행 ────────────────────────────────────────────────

    def run(self):
        self.stop_flag = False
        total_files = 0
        failed_files = 0

        boards = self.find_boards()
        if not boards:
            self.log("❌ 게시판을 찾지 못했습니다. URL과 네트워크 상태를 확인하세요.")
            return

        total_boards = len(boards)
        for b_idx, board in enumerate(boards):
            if self.stop_flag:
                break

            bo_table = board["bo_table"]
            board_name = board["name"]
            board_dir = os.path.join(self.save_dir, self.safe_name(board_name) or bo_table)

            self.log(f"\n{'='*60}")
            self.log(f"[게시판 {b_idx+1}/{total_boards}] {board_name}  (bo_table={bo_table})")

            wr_ids = self.get_post_ids(bo_table, board_name)
            total_posts = len(wr_ids)

            for p_idx, wr_id in enumerate(wr_ids):
                if self.stop_flag:
                    break

                self.log(f"\n  [게시글 {p_idx+1}/{total_posts}] wr_id={wr_id}")
                attachments = self.get_attachments(bo_table, wr_id)

                if not attachments:
                    self.log("    → 첨부파일 없음")
                else:
                    self.log(f"    → 첨부파일 {len(attachments)}개")
                    # 게시글 제목을 폴더명으로
                    post_title = attachments[0]["post_title"]
                    post_dir = os.path.join(
                        board_dir,
                        f"{p_idx+1:04d}_{post_title}"[:80]  # 경로 길이 제한
                    )
                    for att in attachments:
                        if self.stop_flag:
                            break
                        ok = self.download_file(att["url"], post_dir, att["filename"])
                        if ok:
                            total_files += 1
                        else:
                            failed_files += 1

                # 전체 진행률
                done = b_idx * total_posts + p_idx + 1
                total = total_boards * max(total_posts, 1)
                self.set_progress(min(done / total * 100, 99))
                time.sleep(DELAY_SECONDS)

        self.set_progress(100)
        self.log(f"\n{'='*60}")
        self.log(f"완료!  성공: {total_files}개  실패: {failed_files}개")
        self.log(f"저장 위치: {self.save_dir}")


# ────────────────────────────────────────────────────────────────
# GUI
# ────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BUFS 규정집 첨부파일 크롤러")
        self.minsize(760, 580)
        self.resizable(True, True)
        self._crawler: GnuBoardCrawler | None = None
        self._thread: threading.Thread | None = None
        self._build_ui()

    # ── UI 구성 ──────────────────────────────────────────────────

    def _build_ui(self):
        pad = dict(padx=12, pady=6)

        # 설정 영역
        frm = ttk.LabelFrame(self, text="설정", padding=10)
        frm.pack(fill="x", **pad)
        frm.columnconfigure(1, weight=1)

        # URL
        ttk.Label(frm, text="웹사이트 URL:").grid(row=0, column=0, sticky="w", pady=4)
        self.url_var = tk.StringVar(value="https://www.bufs.ac.kr")
        ttk.Entry(frm, textvariable=self.url_var).grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4
        )

        # 저장 폴더
        ttk.Label(frm, text="저장 폴더:").grid(row=1, column=0, sticky="w", pady=4)
        self.dir_var = tk.StringVar(value=os.path.expanduser("~/Downloads/bufs_regulations"))
        ttk.Entry(frm, textvariable=self.dir_var).grid(
            row=1, column=1, sticky="ew", padx=(8, 6), pady=4
        )
        ttk.Button(frm, text="찾아보기...", command=self._pick_dir).grid(
            row=1, column=2, sticky="e", pady=4
        )

        # 게시판 테이블 접두사 (고급)
        ttk.Label(frm, text="bo_table 접두사:").grid(row=2, column=0, sticky="w", pady=4)
        self.prefix_var = tk.StringVar(value="reg_board")
        ttk.Entry(frm, textvariable=self.prefix_var, width=20).grid(
            row=2, column=1, sticky="w", padx=(8, 0), pady=4
        )
        ttk.Label(frm, text="(자동 탐지 실패 시 사용)", foreground="gray").grid(
            row=2, column=2, sticky="w", padx=6
        )

        # 버튼
        frm_btn = ttk.Frame(self)
        frm_btn.pack(fill="x", padx=12, pady=(0, 4))
        self.btn_start = ttk.Button(frm_btn, text="▶  크롤링 시작", command=self._start)
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_stop = ttk.Button(frm_btn, text="⏹  중지", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left")
        ttk.Button(frm_btn, text="로그 지우기", command=self._clear_log).pack(side="right")

        # 진행률
        self.prog_var = tk.DoubleVar()
        ttk.Progressbar(self, variable=self.prog_var, maximum=100).pack(
            fill="x", padx=12, pady=(0, 2)
        )
        self.prog_label = ttk.Label(self, text="대기 중", anchor="w")
        self.prog_label.pack(fill="x", padx=12)

        # 로그
        frm_log = ttk.LabelFrame(self, text="로그", padding=6)
        frm_log.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.log_box = scrolledtext.ScrolledText(
            frm_log, state="disabled", wrap="word",
            font=("Consolas", 9) if os.name == "nt" else ("Monospace", 9),
            bg="#1e1e1e", fg="#d4d4d4",
        )
        self.log_box.pack(fill="both", expand=True)

    # ── 이벤트 ───────────────────────────────────────────────────

    def _pick_dir(self):
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
        self.prog_var.set(0)
        self._log(f"시작: {url}\n저장: {save_dir}\n{'─'*60}\n")

        self._crawler = GnuBoardCrawler(
            base_url=url,
            save_dir=save_dir,
            log_fn=self._log,
            progress_fn=self._set_prog,
        )
        # bo_table 접두사 주입
        self._crawler._probe_prefix = self.prefix_var.get().strip() or "reg_board"

        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()

    def _stop(self):
        if self._crawler:
            self._crawler.stop_flag = True
            self.btn_stop.config(state="disabled")
            self._log("\n⏹ 중지 요청됨 (현재 작업 후 종료)")

    def _run_thread(self):
        try:
            self._crawler.run()
        except Exception as e:
            self._log(f"\n❌ 오류: {e}")
        finally:
            self.after(0, self._done)

    def _done(self):
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    def _log(self, text: str):
        def _do():
            self.log_box.config(state="normal")
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.after(0, _do)

    def _set_prog(self, val: float):
        def _do():
            self.prog_var.set(val)
            self.prog_label.config(text=f"진행률: {val:.1f}%")
        self.after(0, _do)

    def _clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")


# ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
