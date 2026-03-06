// ================================================================
//  Code_web.gs  ─  규정 검색 시스템 API (GitHub Pages 연동)
//  GET ?action=data             → 전체 데이터 JSON 반환
//  GET ?action=clearCache&pw=   → 캐시 초기화 + article 동기화
//  CacheService 활용 (6시간 TTL)
// ================================================================

const CACHE_KEY_REG   = "regulation_data";
const CACHE_KEY_AMEND = "amendment_data";
const CACHE_EXPIRY    = 21600; // 6시간

// ⚠️ 관리자 비밀번호 — 여기서 변경하세요
const ADMIN_PASSWORD  = "admin12345";

function doGet(e) {
  const action = e && e.parameter && e.parameter.action;

  if (action === "data") {
    const data = getAllData();
    return ContentService
      .createTextOutput(JSON.stringify(data))
      .setMimeType(ContentService.MimeType.JSON);
  }

  if (action === "clearCache") {
    const pw     = (e.parameter && e.parameter.pw) || "";
    const result = clearCacheWithPassword(pw);
    return ContentService
      .createTextOutput(JSON.stringify(result))
      .setMimeType(ContentService.MimeType.JSON);
  }

  if (action === "syncPdfUrls") {
    const pw = (e.parameter && e.parameter.pw) || "";
    if (pw !== ADMIN_PASSWORD) {
      return ContentService
        .createTextOutput(JSON.stringify({ success: false, message: "비밀번호가 올바르지 않습니다." }))
        .setMimeType(ContentService.MimeType.JSON);
    }
    const result = syncPdfUrls_();
    return ContentService
      .createTextOutput(JSON.stringify(result))
      .setMimeType(ContentService.MimeType.JSON);
  }

  return ContentService
    .createTextOutput(JSON.stringify({ error: "action 파라미터가 필요합니다. (?action=data)" }))
    .setMimeType(ContentService.MimeType.JSON);
}

// ================================================================
//  전체 데이터 로드 (캐시 우선)
// ================================================================
function getAllData() {
  const cache       = CacheService.getScriptCache();
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

  const data = loadFromSheet();

  try {
    cache.put(CACHE_KEY_REG,     JSON.stringify(data.rows),       CACHE_EXPIRY);
    cache.put(CACHE_KEY_AMEND,   JSON.stringify(data.amendments), CACHE_EXPIRY);
    cache.put("regulation_meta", JSON.stringify(data.regMeta),    CACHE_EXPIRY);
  } catch(e) {
    Logger.log("캐시 저장 실패: " + e.message);
  }

  return { rows: data.rows, amendments: data.amendments, regMeta: data.regMeta, fromCache: false };
}

// ================================================================
//  시트에서 직접 읽기
// ================================================================
function loadFromSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // ── 1. regulations 시트 → reg_id/name 맵 + regMeta ──────────
  const regSheet = ss.getSheetByName("regulations");
  const regRaw   = regSheet ? regSheet.getDataRange().getValues() : [];
  const regIdToName = {};
  const regMeta     = {};

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
        const regId   = String(rg(r, iRId)  || "").trim();
        const regName = String(rg(r, iRName) || "").trim();
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

  const iRegId      = ac("reg_id");
  const iArtNo      = ac("article_no");
  const iArtTit     = ac("article_title");
  const iChapter    = ac("chapter");
  const iChTitle    = ac("chapter_title");
  const iSection    = ac("section");
  const iSecTit     = ac("section_title");
  const iPara       = ac("paragraph_no");
  const iItem       = ac("item_no");
  const iSub        = ac("subitem_no");
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
        amendType      : String(hg(r, iHType) || ""),
        effectDate     : "",
        content        : String(hg(r, iHCont) || "")
      };
    });
  }

  return { rows, amendments, regMeta };
}

// ================================================================
//  Drive PDF URL 자동 매핑 (관리자용)
// ================================================================
function syncPdfUrls_() {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const regSheet = ss.getSheetByName("regulations");
    if (!regSheet) return { success: false, message: "regulations 시트 없음" };

    const data = regSheet.getDataRange().getValues();
    if (data.length < 2) return { success: false, message: "regulations 데이터 없음" };

    const headers = data[0].map(h => String(h).toLowerCase().trim());
    const iName   = headers.indexOf("reg_name");
    const iPdf    = headers.indexOf("pdf_url");
    if (iName < 0 || iPdf < 0) return { success: false, message: "reg_name 또는 pdf_url 컬럼 없음" };

    // Drive 전체 검색: 이름에 규정명 포함, PDF 파일
    let updated = 0;
    for (let i = 1; i < data.length; i++) {
      const regName = String(data[i][iName] || "").trim();
      if (!regName) continue;
      if (data[i][iPdf]) continue; // 이미 URL 있음

      const files = DriveApp.searchFiles(
        'mimeType="application/pdf" and title contains "' + regName.replace(/"/g, "") + '" and trashed=false'
      );
      if (files.hasNext()) {
        const file = files.next();
        file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
        const url = "https://drive.google.com/file/d/" + file.getId() + "/view";
        regSheet.getRange(i + 1, iPdf + 1).setValue(url);
        updated++;
      }
    }

    // 캐시 갱신 필요
    const cache = CacheService.getScriptCache();
    cache.remove("regulation_meta");
    cache.remove(CACHE_KEY_REG);

    return { success: true, message: "PDF 링크 " + updated + "건 매핑 완료. 캐시도 초기화했습니다." };
  } catch(e) {
    return { success: false, message: "오류: " + e.message };
  }
}

// ================================================================
//  article_history → article 동기화 (내부용)
// ================================================================
function syncArticle_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  const ahSheet = ss.getSheetByName("article_history");
  if (!ahSheet) return { success: false, message: "article_history 시트 없음" };

  const ahData = ahSheet.getDataRange().getValues();
  if (ahData.length < 2) return { success: false, message: "article_history 데이터 없음" };

  const ah = ahData[0].map(h => String(h).toLowerCase().trim());
  const c  = name => ah.indexOf(name);

  const artRevMap = {};
  for (let i = 1; i < ahData.length; i++) {
    const r     = ahData[i];
    const regId = String(r[c("reg_id")]      || "").trim();
    const artNo = safeArticleNo(r[c("article_no")]);
    const ctype = String(r[c("change_type")] || "").trim();
    const cdate = formatDate(r[c("change_date")]);   // Date 객체도 정규화

    if (!regId || !artNo || !ctype || !cdate) continue;

    const key = regId + "||" + artNo;
    if (!artRevMap[key]) artRevMap[key] = {};
    if (!artRevMap[key][ctype]) artRevMap[key][ctype] = [];
    if (artRevMap[key][ctype].indexOf(cdate) < 0) artRevMap[key][ctype].push(cdate);
  }

  function formatRevDates(typeMap) {
    if (!typeMap) return "";
    if (typeMap["삭제"] && typeMap["삭제"].length > 0) {
      return "<" + typeMap["삭제"].sort()[0] + ">";
    }
    // 본조신설/전부개정은 full.html의 artLevelAnno에서 조 수준으로 따로 표시
    const ORDER = ["신설", "개정"];
    const parts = [];
    for (let ti = 0; ti < ORDER.length; ti++) {
      const ctype = ORDER[ti];
      if (!typeMap[ctype] || typeMap[ctype].length === 0) continue;
      parts.push("(" + ctype + " " + typeMap[ctype].slice().sort().join(", ") + ")");
    }
    return parts.join(" ");
  }

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
    const syncResult = syncArticle_();
    const syncMsg    = syncResult.success
      ? "article 동기화 완료 (" + syncResult.count + "행)."
      : "article 동기화 실패: " + syncResult.message;

    const cache = CacheService.getScriptCache();
    cache.remove(CACHE_KEY_REG);
    cache.remove(CACHE_KEY_AMEND);
    cache.remove("regulation_meta");

    return {
      success: true,
      message: syncMsg + " 캐시 초기화 완료. 페이지를 새로고침하면 최신 데이터가 반영됩니다."
    };
  } catch(e) {
    return { success: false, message: "초기화 실패: " + e.message };
  }
}

// ================================================================
//  조번호 안전 읽기 (Sheets가 "2-2"를 날짜로 자동변환하는 경우 복원)
// ================================================================
function safeArticleNo(val) {
  if (val instanceof Date) {
    return (val.getMonth() + 1) + "-" + val.getDate();
  }
  return String(val || "").trim();
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
  const mLoose = s.match(/(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})/);
  if (mKor)   return mKor[1]   + "." + String(mKor[2]).padStart(2,"0")   + "." + String(mKor[3]).padStart(2,"0")   + ".";
  if (mDash)  return mDash[1]  + "." + mDash[2]  + "." + mDash[3]  + ".";
  if (mDot)   return mDot[1]   + "." + mDot[2]   + "." + mDot[3]   + ".";
  if (mLoose) return mLoose[1] + "." + String(mLoose[2]).padStart(2,"0") + "." + String(mLoose[3]).padStart(2,"0") + ".";
  return s;
}
