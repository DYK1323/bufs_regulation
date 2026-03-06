// ================================================================
//  Code_web.gs  ─  규정 키워드 검색 + 전문 조회 웹앱
//  최적화: CacheService + 브라우저 검색 처리
//  관리자: 비밀번호 인증 후 캐시 초기화 가능
// ================================================================

const CACHE_KEY_REG   = "regulation_data";
const CACHE_KEY_AMEND = "amendment_data";
const CACHE_EXPIRY    = 21600; // 6시간

// ⚠️ 관리자 비밀번호 — 여기서 변경하세요
const ADMIN_PASSWORD  = "admin12345";

// ⚠️ PDF 파일을 업로드한 Google Drive 폴더 ID — 여기서 설정하세요
// 폴더 URL: https://drive.google.com/drive/folders/XXXX 에서 XXXX 부분
const PDF_FOLDER_ID   = "1aUOl9pRDLDVG6aowyNAug-iwG50NvEY1";

function doGet(e) {
  const page = e && e.parameter && e.parameter.page;
  if (page === "full") {
    return HtmlService
      .createHtmlOutputFromFile("full")
      .setTitle("규정 전문")
      .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
  }
  return HtmlService
    .createHtmlOutputFromFile("index")
    .setTitle("규정 검색 시스템")
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

// ================================================================
//  전체 데이터 로드 (캐시 우선)
// ================================================================
function getAllData() {

  const cache = CacheService.getScriptCache();
  const cachedReg   = cache.get(CACHE_KEY_REG);
  const cachedAmend = cache.get(CACHE_KEY_AMEND);
  const cachedMeta  = cache.get("regulation_meta");

  if (cachedReg && cachedAmend) {
    return {
      rows       : JSON.parse(cachedReg),
      amendments : JSON.parse(cachedAmend),
      regMeta    : cachedMeta ? JSON.parse(cachedMeta) : {},
      fromCache  : true
    };
  }

  // 캐시 없으면 시트에서 읽고 캐시 저장
  const data = loadFromSheet();

  try {
    cache.put(CACHE_KEY_REG,   JSON.stringify(data.rows),       CACHE_EXPIRY);
    cache.put(CACHE_KEY_AMEND, JSON.stringify(data.amendments), CACHE_EXPIRY);
    cache.put("regulation_meta", JSON.stringify(data.regMeta),  CACHE_EXPIRY);
  } catch(e) {
    Logger.log("캐시 저장 실패: " + e.message);
  }

  return { rows: data.rows, amendments: data.amendments, regMeta: data.regMeta, fromCache: false };
}

// ================================================================
//  시트에서 직접 읽기  (v4 스키마: article / history / regulations)
// ================================================================
function loadFromSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // ── 1. regulations 시트 → reg_id/name 맵 + regMeta ──────────
  const regSheet = ss.getSheetByName("regulations");
  const regRaw   = regSheet ? regSheet.getDataRange().getValues() : [];
  const regIdToName = {};  // { "2101": "학칙", ... }
  const regMeta     = {};  // { "학칙": { pdfUrl, enactDate, ... } }

  if (regRaw.length >= 2) {
    const rh       = regRaw[0].map(h => String(h).toLowerCase().trim());
    const iRId     = rh.indexOf("reg_id");
    const iRName   = rh.indexOf("reg_name");
    const iPdfUrl  = rh.indexOf("pdf_url");
    const iEnact   = rh.indexOf("enacted_date");
    const iRevised = rh.indexOf("revised_date");
    const iEff     = rh.indexOf("effective_date");
    const rg       = (r, i) => i >= 0 ? r[i] : "";

    if (iRId >= 0 && iRName >= 0) {
      regRaw.slice(1).forEach(r => {
        const regId   = String(rg(r, iRId)   || "").trim();
        const regName = String(rg(r, iRName)  || "").trim();
        if (!regId || !regName) return;
        regIdToName[regId] = regName;
        regMeta[regName] = {
          pdfUrl        : String(rg(r, iPdfUrl)  || "").trim(),
          enactDate     : String(rg(r, iEnact)   || "").trim(),
          lastAmendDate : String(rg(r, iRevised) || "").trim(),
          lastAmendType : "",
          lastEffDate   : String(rg(r, iEff)     || "").trim(),
        };
      });
    }
  }

  // ── 2. article 시트 → rows ───────────────────────────────────
  const artSheet = ss.getSheetByName("article");
  const artRaw   = artSheet ? artSheet.getDataRange().getValues() : [];

  if (artRaw.length < 2) return { rows: [], amendments: [], regMeta };

  const ah = artRaw[0].map(h => String(h).toLowerCase().trim());
  const ac = name => ah.indexOf(name);
  const ag = (r, i) => i >= 0 ? r[i] : "";

  const iRegId    = ac("reg_id");
  const iArtNo    = ac("article_no");
  const iArtTit   = ac("article_title");
  const iChapter  = ac("chapter");
  const iChTitle  = ac("chapter_title");
  const iSection  = ac("section");
  const iSecTit   = ac("section_title");
  const iPara     = ac("paragraph_no");
  const iItem     = ac("item_no");
  const iSub      = ac("subitem_no");
  const iCont       = ac("content");
  const iSort       = ac("sort_key");
  const iRevDates   = ac("revision_dates");
  const iChangeType = ac("change_type");
  const iChangeDate = ac("change_date");

  const rows = artRaw.slice(1).map(r => {
    const regId = String(ag(r, iRegId) || "").trim();
    return {
      regulationName : regIdToName[regId] || regId,
      chapter        : String(ag(r, iChapter)    || "").trim(),
      chapterTitle   : String(ag(r, iChTitle)     || "").trim(),
      section        : String(ag(r, iSection)     || "").trim(),
      sectionTitle   : String(ag(r, iSecTit)      || "").trim(),
      articleNo      : safeArticleNo(ag(r, iArtNo)),
      articleTitle   : String(ag(r, iArtTit)    || "").trim(),
      paragraphNo    : ag(r, iPara),
      itemNo         : ag(r, iItem),
      subitemNo      : ag(r, iSub),
      content        : String(ag(r, iCont)      || ""),
      sortKey        : String(ag(r, iSort)      || ""),
      revisionDates  : String(ag(r, iRevDates)  || "").trim(),
      changeType     : String(ag(r, iChangeType) || "").trim(),
      changeDate     : String(ag(r, iChangeDate) || "").trim(),
    };
  });

  // ── 3. history 시트 → amendments ────────────────────────────
  const histSheet = ss.getSheetByName("history");
  const histRaw   = histSheet ? histSheet.getDataRange().getValues() : [];
  let amendments  = [];

  if (histRaw.length >= 2) {
    const hh      = histRaw[0].map(h => String(h).toLowerCase().trim());
    const iHRegId = hh.indexOf("reg_id");
    const iHName  = hh.indexOf("reg_name");
    const iHRound = hh.indexOf("round_id");
    const iHDate  = hh.indexOf("change_date");
    const iHType  = hh.indexOf("change_type");
    const iHCont  = hh.indexOf("content");
    const hg      = (r, i) => i >= 0 ? r[i] : "";

    amendments = histRaw.slice(1).map(r => {
      const regId   = String(hg(r, iHRegId) || "").trim();
      const regName = iHName >= 0
        ? String(hg(r, iHName) || "").trim()
        : (regIdToName[regId] || regId);
      return {
        regulationName : regName,
        seq            : Number(hg(r, iHRound)) || 0,
        amendDate      : formatDate(hg(r, iHDate)),
        amendType      : String(hg(r, iHType)   || ""),
        effectDate     : "",
        content        : String(hg(r, iHCont)   || "")
      };
    });
  }

  return { rows, amendments, regMeta };
}

// ================================================================
//  article_history → article 동기화 (내부용)
//  article_history 의 record_status="active" 행만 article 시트에 기록.
//  revision_dates: article_history의 change_type/change_date를 조 단위로 집계.
//    · 신설/개정/본조신설/전부개정 → (타입 date1, date2, ...)
//    · 삭제 → <date>  (삭제된 경우 해당 표기만)
// ================================================================
function syncArticle_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // 1. article_history 읽기
  const ahSheet = ss.getSheetByName("article_history");
  if (!ahSheet) return { success: false, message: "article_history 시트 없음" };

  const ahData = ahSheet.getDataRange().getValues();
  if (ahData.length < 2) return { success: false, message: "article_history 데이터 없음" };

  const ah = ahData[0].map(h => String(h).toLowerCase().trim());
  const c  = name => ah.indexOf(name);

  // Phase 1: (reg_id + article_no) 별로 change_type → dates 수집
  // key: "regId||artNo"  →  { "신설": [dates], "개정": [dates], ... }
  // ※ superseded 포함 — 전체 개정 이력을 revision_dates에 반영하기 위해
  const artRevMap = {};

  for (let i = 1; i < ahData.length; i++) {
    const r     = ahData[i];
    const regId = String(r[c("reg_id")]      || "").trim();
    const artNo = safeArticleNo(r[c("article_no")]);
    const ctype = String(r[c("change_type")] || "").trim();
    const cdate = String(r[c("change_date")] || "").trim();

    if (!regId || !artNo || !ctype || ctype === "신규" || !cdate) continue;

    const key = regId + "||" + artNo;
    if (!artRevMap[key]) artRevMap[key] = {};
    if (!artRevMap[key][ctype]) artRevMap[key][ctype] = [];
    if (artRevMap[key][ctype].indexOf(cdate) < 0) artRevMap[key][ctype].push(cdate);
  }

  // Phase 2: 포맷 함수
  // 삭제가 있으면 <date> 하나만; 아니면 타입별 그룹 나열
  function formatRevDates(typeMap) {
    if (!typeMap) return "";
    if (typeMap["삭제"] && typeMap["삭제"].length > 0) {
      return "<" + typeMap["삭제"].sort()[0] + ">";
    }
    const ORDER = ["신설", "본조신설", "전부개정", "개정"];
    const parts = [];
    for (let ti = 0; ti < ORDER.length; ti++) {
      const ctype = ORDER[ti];
      if (!typeMap[ctype] || typeMap[ctype].length === 0) continue;
      const dates = typeMap[ctype].slice().sort();
      parts.push("(" + ctype + " " + dates.join(", ") + ")");
    }
    return parts.join(" ");
  }

  // Phase 3: article 시트 행 생성 (active 행만)
  const ARTICLE_COLS = [
    "reg_id", "article_no", "article_title",
    "chapter", "chapter_title", "section", "section_title",
    "paragraph_no", "item_no", "subitem_no",
    "content", "version", "revision_dates", "sort_key",
    "change_type", "change_date"
  ];
  const out = [ARTICLE_COLS];

  for (let i = 1; i < ahData.length; i++) {
    const r      = ahData[i];
    const status = c("record_status") >= 0 ? String(r[c("record_status")] || "").trim() : "active";
    if (status !== "active") continue;

    const regId = String(r[c("reg_id")] || "").trim();
    const artNo = safeArticleNo(r[c("article_no")]);
    const key   = regId + "||" + artNo;

    out.push([
      regId,
      artNo,
      String(r[c("article_title")] || "").trim(),
      String(r[c("chapter")]       || "").trim(),
      String(r[c("chapter_title")] || "").trim(),
      String(r[c("section")]       || "").trim(),
      String(r[c("section_title")] || "").trim(),
      c("paragraph_no") >= 0 ? r[c("paragraph_no")] : "",
      String(r[c("item_no")]    || "").trim(),
      String(r[c("subitem_no")] || "").trim(),
      String(r[c("content")]    || ""),
      c("version") >= 0 ? r[c("version")] : "",
      formatRevDates(artRevMap[key]),
      String(r[c("sort_key")]    || "").trim(),
      String(r[c("change_type")] || "").trim(),
      String(r[c("change_date")] || "").trim(),
    ]);
  }

  // 4. article 시트 덮어쓰기
  let artSheet = ss.getSheetByName("article");
  if (!artSheet) {
    artSheet = ss.insertSheet("article");
  } else {
    artSheet.clearContents();
  }
  if (out.length > 1) {
    artSheet.getRange(1, 1, out.length, ARTICLE_COLS.length).setValues(out);
    artSheet.setFrozenRows(1);
  }

  return { success: true, count: out.length - 1 };
}

// ================================================================
//  관리자 캐시 초기화 (비밀번호 인증) + article 동기화
// ================================================================
function clearCacheWithPassword(password) {
  if (password !== ADMIN_PASSWORD) {
    return { success: false, message: "비밀번호가 올바르지 않습니다." };
  }

  try {
    // article 시트 동기화 (active 행만 추출)
    const syncResult = syncArticle_();
    const syncMsg    = syncResult.success
      ? `article 동기화 완료 (${syncResult.count}행).`
      : `article 동기화 실패: ${syncResult.message}`;

    // 캐시 전체 초기화
    const cache = CacheService.getScriptCache();
    cache.remove(CACHE_KEY_REG);
    cache.remove(CACHE_KEY_AMEND);
    cache.remove("regulation_meta");

    return {
      success: true,
      message: `${syncMsg} 캐시 초기화 완료. 페이지를 새로고침하면 최신 데이터가 반영됩니다.`
    };
  } catch(e) {
    return { success: false, message: "초기화 실패: " + e.message };
  }
}

// ================================================================
//  PDF 링크 자동 매핑 (Drive 폴더 스캔 → regulation_meta.pdf_url 갱신)
// ================================================================
function syncPdfUrls(password) {
  if (password !== ADMIN_PASSWORD) {
    return { success: false, message: "비밀번호가 올바르지 않습니다." };
  }
  if (!PDF_FOLDER_ID) {
    return { success: false, message: "Code_web.js의 PDF_FOLDER_ID를 먼저 설정하세요." };
  }

  try {
    // Drive 폴더 내 파일명 → URL 맵 구성
    const folder  = DriveApp.getFolderById(PDF_FOLDER_ID);
    const files   = folder.getFiles();
    const fileMap = {};
    while (files.hasNext()) {
      const f = files.next();
      fileMap[f.getName()] = f.getUrl();
    }

    // regulations 시트 갱신
    const ss       = SpreadsheetApp.getActiveSpreadsheet();
    const regSheet = ss.getSheetByName("regulations");
    if (!regSheet) return { success: false, message: "regulations 시트가 없습니다." };

    const data   = regSheet.getDataRange().getValues();
    const header = data[0].map(h => String(h).toLowerCase().trim());
    const iFile  = header.indexOf("pdf_filename");
    const iUrl   = header.indexOf("pdf_url");
    if (iFile < 0 || iUrl < 0) return { success: false, message: "regulations 시트에 pdf_filename/pdf_url 컬럼이 없습니다." };

    let matched = 0;
    for (let i = 1; i < data.length; i++) {
      const fname = String(data[i][iFile] || "").trim();
      if (fname && fileMap[fname]) {
        regSheet.getRange(i + 1, iUrl + 1).setValue(fileMap[fname]);
        matched++;
      }
    }

    // 캐시 초기화 (다음 조회 시 최신 반영)
    CacheService.getScriptCache().remove("regulation_meta");

    return {
      success: true,
      message: `Drive 파일 ${Object.keys(fileMap).length}개 스캔, ${matched}개 매핑 완료.`
    };
  } catch(e) {
    return { success: false, message: "오류: " + e.message };
  }
}

// ================================================================
//  조번호 안전 읽기 (Sheets가 "2-2"를 날짜로 자동변환하는 경우 복원)
// ================================================================
function safeArticleNo(val) {
  if (val instanceof Date) {
    // "2-2" → Date(2026-02-02) → "2-2" 복원
    return (val.getMonth() + 1) + "-" + val.getDate();
  }
  return String(val || "").trim();
}

// ================================================================
//  웹앱 URL 반환
// ================================================================
function getScriptUrl() {
  return ScriptApp.getService().getUrl();
}

// ================================================================
//  날짜 포맷
// ================================================================
function formatDate(raw) {
  if (!raw) return "";
  if (raw instanceof Date) {
    const y = raw.getFullYear();
    const m = String(raw.getMonth() + 1).padStart(2, "0");
    const d = String(raw.getDate()).padStart(2, "0");
    return y + "." + m + "." + d + ".";
  }
  const s      = String(raw).trim();
  const mKor   = s.match(/(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일/);
  const mDash  = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  const mDot   = s.match(/^(\d{4})\.(\d{2})\.(\d{2})\.?$/);
  // "개정 YYYY. M. D." 또는 "개정YYYY. M. D." 등 접두사 포함 형식
  const mLoose = s.match(/(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})/);
  if (mKor)   return mKor[1]   + "." + String(mKor[2]).padStart(2,"0")   + "." + String(mKor[3]).padStart(2,"0")   + ".";
  if (mDash)  return mDash[1]  + "." + mDash[2]  + "." + mDash[3]  + ".";
  if (mDot)   return mDot[1]   + "." + mDot[2]   + "." + mDot[3]   + ".";
  if (mLoose) return mLoose[1] + "." + String(mLoose[2]).padStart(2,"0") + "." + String(mLoose[3]).padStart(2,"0") + ".";
  return s;
}