"""
표 추출 및 숫자 분석 모듈
- 표를 pandas DataFrame으로 변환
- 숫자/금액/비율/연도/기간 자동 탐지
- 표별 요약 정보 생성
- HWPX 표 그리드 파싱 — 병합 셀(cellAddr/cellSpan) 복원
"""

import re
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree as ET


@dataclass
class NumberInfo:
    """탐지된 숫자 정보"""
    value: str  # 원본 텍스트
    numeric_value: Optional[float] = None  # 변환된 숫자
    category: str = ""  # money, percentage, year, period, count
    unit: str = ""  # 원, 천원, 백만원, %, 년 등
    context: str = ""  # 주변 텍스트
    source: str = ""  # "table" or "text"
    table_index: int = -1
    row: int = -1
    col: int = -1
    document_id: str = ""


@dataclass
class TableSummary:
    """표 요약 정보"""
    index: int = 0
    caption: str = ""
    unit: str = ""
    num_rows: int = 0
    num_cols: int = 0
    headers: list = field(default_factory=list)
    numeric_columns: list = field(default_factory=list)
    money_columns: list = field(default_factory=list)
    year_columns: list = field(default_factory=list)
    has_total_row: bool = False
    total_row_index: int = -1
    dataframe: Optional[pd.DataFrame] = None
    preview: str = ""
    document_id: str = ""
    confidence: float = 1.0
    warnings: list = field(default_factory=list)
    header_row_count: int = 1
    unit_multiplier: float = 1.0


# 숫자 탐지 패턴
PATTERNS = {
    'money_with_unit': re.compile(
        r'([\d,]+(?:\.\d+)?)\s*(원|천원|만원|백만원|억원|조원|천만원|십억원)'
    ),
    'money_plain': re.compile(
        r'([\d,]{4,}(?:\.\d+)?)'  # 4자리 이상 콤마 포함 숫자
    ),
    'percentage': re.compile(
        r'([\d,]+(?:\.\d+)?)\s*(%|퍼센트|프로)'
    ),
    'year': re.compile(
        r'((?:19|20)\d{2})\s*(?:년|\.)'
    ),
    'period': re.compile(
        r'((?:19|20)\d{2})\s*[~\-–—]\s*((?:19|20)\d{2})'
    ),
    'ratio': re.compile(
        r'(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)'
    ),
}

TOTAL_KEYWORDS = ['합계', '총계', '소계', '계', '합', '총', 'total', 'sum', '전체']
BUDGET_KEYWORDS = ['예산', '사업비', '총액', '금액', '비용', '단가', '투자', '집행', '배정', '재원']
UNIT_MULTIPLIERS = {
    '원': 1,
    '천원': 1000,
    '만원': 10000,
    '백만원': 1000000,
    '천만원': 10000000,
    '억원': 100000000,
    '십억원': 1000000000,
    '조원': 1000000000000,
}


UNIT_ROW_PATTERN = re.compile(r'[\(\（]?\s*단위\s*[:：]\s*([^)\）]+)', re.IGNORECASE)


def extract_tables(parsed_doc, document_id: str = "") -> list[TableSummary]:
    """ParsedDocument에서 표를 추출하고 요약"""
    summaries = []

    for idx, raw_table in enumerate(parsed_doc.tables_raw):
        rows = raw_table.get('rows', [])
        if not rows:
            continue

        df, unit_from_row, header_count = _rows_to_dataframe(rows)

        unit = raw_table.get('unit', '') or unit_from_row
        multiplier = UNIT_MULTIPLIERS.get(unit, 1.0)

        summary = TableSummary(
            index=idx,
            caption=raw_table.get('caption', ''),
            unit=unit,
            num_rows=df.shape[0] if df is not None else len(rows),
            num_cols=df.shape[1] if df is not None else (max(len(r) for r in rows) if rows else 0),
            document_id=document_id,
            header_row_count=header_count,
            unit_multiplier=float(multiplier),
        )

        summary.dataframe = df
        summary.headers = list(df.columns) if df is not None else []

        if df is not None and not df.empty:
            summary.numeric_columns = _find_numeric_columns(df)
            summary.money_columns = _find_money_columns(df)
            summary.year_columns = _find_year_columns(df)
            summary.has_total_row, summary.total_row_index = _find_total_row(df)
            summary.preview = df.head(5).to_string(index=False)

        summary.confidence, summary.warnings = _calculate_confidence(
            df, summary.headers, summary.numeric_columns)

        summaries.append(summary)

    return summaries


def _is_unit_row(row: list) -> tuple[bool, str]:
    """첫 행이 단위 정보만 담고 있는지 확인"""
    non_empty = [c for c in row if str(c).strip()]
    if len(non_empty) <= 1:
        text = ' '.join(str(c) for c in row).strip()
        m = UNIT_ROW_PATTERN.search(text)
        if m:
            return True, m.group(1).strip()
    return False, ""


def _detect_header_rows(normalized: list, header_start: int) -> int:
    """다중 헤더 행 감지. 연속으로 헤더 조건을 만족하는 행 수 반환."""
    remaining_data = len(normalized) - header_start
    if remaining_data < 3:
        return 1

    count = 0
    for i in range(header_start, min(header_start + 3, len(normalized))):
        row = normalized[i]
        non_empty = [str(c).strip() for c in row if str(c).strip()]
        if not non_empty:
            break
        numeric_count = sum(1 for c in non_empty
                           if _is_number(c.replace(',', '').replace(' ', '')))
        avg_len = sum(len(c) for c in non_empty) / len(non_empty)
        if numeric_count / len(non_empty) < 0.3 and avg_len < 20:
            count += 1
        else:
            break

    if len(normalized) - header_start - count < 1:
        return 1

    return max(1, count)


def _merge_header_rows(header_rows: list) -> list[str]:
    """다중 헤더 행을 '_'로 병합.
    최상위 행: 빈 셀은 왼쪽 값으로 forward-fill (병합 셀 복원).
    하위 행: 상위 행이 같은 그룹(같은 값)일 때만 forward-fill."""
    num_cols = len(header_rows[0])

    top_row = [str(c).strip() for c in header_rows[0]]
    for i in range(1, num_cols):
        if not top_row[i]:
            top_row[i] = top_row[i - 1]

    filled_rows = [top_row]
    for row in header_rows[1:]:
        filled = [str(c).strip() for c in row]
        for i in range(1, min(num_cols, len(filled))):
            if not filled[i] and top_row[i] == top_row[i - 1]:
                filled[i] = filled[i - 1]
        filled_rows.append(filled)

    merged = []
    for col_idx in range(num_cols):
        parts = []
        for filled in filled_rows:
            val = filled[col_idx] if col_idx < len(filled) else ''
            if val and val not in parts:
                parts.append(val)
        merged.append('_'.join(parts) if parts else f'열{col_idx + 1}')

    return merged


def _calculate_confidence(df: Optional[pd.DataFrame], headers: list,
                          numeric_cols: list) -> tuple[float, list]:
    """표 품질 신뢰도 계산. (score, warnings) 반환."""
    score = 1.0
    warnings = []

    auto_count = sum(1 for h in headers if re.match(r'^열\d+$', str(h)))
    if headers and auto_count / len(headers) > 0.5:
        score -= 0.3
        warnings.append("헤더 인식 실패 가능성 (자동 생성 헤더 많음)")

    if df is not None and not df.empty:
        total_cells = df.shape[0] * df.shape[1]
        if total_cells > 0:
            empty = sum(1 for col in df.columns
                        for val in df[col] if not str(val).strip())
            if empty / total_cells > 0.5:
                score -= 0.2
                warnings.append("빈 셀 비율 높음 (50% 이상)")

        if df.shape[0] <= 1:
            score -= 0.1
            warnings.append("데이터 행 부족 (1행 이하)")

    if not numeric_cols:
        score -= 0.1
        warnings.append("숫자 컬럼 없음")

    return round(max(0.0, min(1.0, score)), 2), warnings


def _rows_to_dataframe(rows: list) -> tuple[Optional[pd.DataFrame], str, int]:
    """표 행 데이터를 DataFrame으로 변환. (df, unit_from_row, header_count) 반환"""
    if not rows:
        return None, "", 1

    unit_from_row = ""

    max_cols = max(len(r) for r in rows)
    normalized = []
    for r in rows:
        padded = r + [''] * (max_cols - len(r))
        normalized.append(padded)

    header_start = 0
    is_unit, unit_val = _is_unit_row(normalized[0])
    if is_unit and len(normalized) >= 3:
        unit_from_row = unit_val
        header_start = 1

    if len(normalized) - header_start >= 2:
        header_count = _detect_header_rows(normalized, header_start)

        if header_count > 1:
            header_rows = normalized[header_start:header_start + header_count]
            raw_headers = _merge_header_rows(header_rows)
        else:
            raw_headers = [str(h).strip() if h else '' for h in normalized[header_start]]

        seen = {}
        unique_headers = []
        for h in raw_headers:
            if not h:
                h = f'열{len(unique_headers)+1}'
            if h in seen:
                seen[h] += 1
                h = f'{h}_{seen[h]}'
            else:
                seen[h] = 0
            unique_headers.append(h)

        data = normalized[header_start + header_count:]
        df = pd.DataFrame(data, columns=unique_headers)
    else:
        df = pd.DataFrame(normalized)
        header_count = 0

    return df, unit_from_row, header_count


def _find_numeric_columns(df: pd.DataFrame) -> list:
    numeric_cols = []
    for col in df.columns:
        values = df[col].astype(str)
        numeric_count = 0
        for v in values:
            if v.strip() and _is_number(v):
                numeric_count += 1
        if numeric_count > len(values) * 0.3:
            numeric_cols.append(str(col))
    return numeric_cols


def _find_money_columns(df: pd.DataFrame) -> list:
    """금액 관련 컬럼 찾기"""
    money_cols = []
    for col in df.columns:
        col_str = str(col).lower()
        if any(kw in col_str for kw in BUDGET_KEYWORDS):
            money_cols.append(str(col))
            continue
        values = df[col].astype(str)
        money_count = sum(
            1 for v in values
            if PATTERNS['money_with_unit'].search(v)
        )
        if money_count > len(values) * 0.2:
            money_cols.append(str(col))
    return money_cols


def _find_year_columns(df: pd.DataFrame) -> list:
    """연도 관련 컬럼 찾기"""
    year_cols = []
    for col in df.columns:
        col_str = str(col)
        if PATTERNS['year'].search(col_str) or PATTERNS['period'].search(col_str):
            year_cols.append(col_str)
            continue
        if re.match(r'^(19|20)\d{2}$', col_str.strip()):
            year_cols.append(col_str)
    return year_cols


def _find_total_row(df: pd.DataFrame) -> tuple[bool, int]:
    """합계 행 찾기"""
    for idx, row in df.iterrows():
        for val in row:
            val_str = str(val).strip().lower()
            if val_str in TOTAL_KEYWORDS:
                return True, int(idx)
    return False, -1


FULLWIDTH_TABLE = str.maketrans(
    '０１２３４５６７８９．，　',
    '0123456789., ',
)


def _normalize_number_str(s: str) -> str:
    s = s.translate(FULLWIDTH_TABLE)
    s = s.replace(' ', ' ').replace(' ', '').replace(' ', '')
    s = s.replace(',', '').replace(' ', '').strip()
    if s.startswith('(') and s.endswith(')'):
        inner = s[1:-1].strip()
        if inner and all(c in '0123456789.' for c in inner):
            s = '-' + inner
    s = re.sub(r'[원천만백억조%명개건호]', '', s)
    if s.startswith('△') or s.startswith('▲'):
        s = '-' + s[1:]
    return s


def _is_number(s: str) -> bool:
    try:
        cleaned = _normalize_number_str(s)
        if not cleaned:
            return False
        float(cleaned)
        return True
    except ValueError:
        return False


def detect_numbers_in_text(text: str, document_id: str = "") -> list[NumberInfo]:
    """텍스트에서 숫자/금액/비율/연도 탐지"""
    results = []

    for match in PATTERNS['money_with_unit'].finditer(text):
        value_str = match.group(1).replace(',', '')
        unit = match.group(2)
        try:
            numeric = float(value_str) * UNIT_MULTIPLIERS.get(unit, 1)
        except ValueError:
            numeric = None

        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 20)
        results.append(NumberInfo(
            value=match.group(0),
            numeric_value=numeric,
            category='money',
            unit=unit,
            context=text[start:end].strip(),
            source='text',
            document_id=document_id,
        ))

    for match in PATTERNS['percentage'].finditer(text):
        value_str = match.group(1).replace(',', '')
        try:
            numeric = float(value_str)
        except ValueError:
            numeric = None
        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 20)
        results.append(NumberInfo(
            value=match.group(0),
            numeric_value=numeric,
            category='percentage',
            unit='%',
            context=text[start:end].strip(),
            source='text',
            document_id=document_id,
        ))

    for match in PATTERNS['period'].finditer(text):
        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 20)
        results.append(NumberInfo(
            value=match.group(0),
            numeric_value=None,
            category='period',
            unit='년',
            context=text[start:end].strip(),
            source='text',
            document_id=document_id,
        ))

    for match in PATTERNS['year'].finditer(text):
        already_in_period = any(
            n.category == 'period' and match.group(1) in n.value
            for n in results
        )
        if not already_in_period:
            start = max(0, match.start() - 20)
            end = min(len(text), match.end() + 20)
            results.append(NumberInfo(
                value=match.group(0),
                numeric_value=float(match.group(1)),
                category='year',
                unit='년',
                context=text[start:end].strip(),
                source='text',
                document_id=document_id,
            ))

    return results


def detect_numbers_in_tables(table_summaries: list[TableSummary], document_id: str = "") -> list[NumberInfo]:
    """표에서 숫자/금액 탐지"""
    results = []

    for ts in table_summaries:
        if ts.dataframe is None:
            continue
        df = ts.dataframe
        for row_idx, row in df.iterrows():
            for col_idx, col in enumerate(df.columns):
                cell = str(row[col]).strip()
                if not cell:
                    continue

                for match in PATTERNS['money_with_unit'].finditer(cell):
                    value_str = match.group(1).replace(',', '')
                    unit = match.group(2)
                    try:
                        numeric = float(value_str) * UNIT_MULTIPLIERS.get(unit, 1)
                    except ValueError:
                        numeric = None
                    results.append(NumberInfo(
                        value=match.group(0),
                        numeric_value=numeric,
                        category='money',
                        unit=unit,
                        context=f"표{ts.index+1} [{col}] 행{int(row_idx)+1}",
                        source='table',
                        table_index=ts.index,
                        row=int(row_idx),
                        col=col_idx,
                        document_id=document_id or ts.document_id,
                    ))

                if not PATTERNS['money_with_unit'].search(cell):
                    cleaned = cell.replace(',', '').strip()
                    if _is_number(cleaned) and len(cleaned) >= 3:
                        try:
                            numeric = float(cleaned)
                        except ValueError:
                            numeric = None
                        col_name = str(col)
                        cat = 'money' if any(k in col_name for k in BUDGET_KEYWORDS) else 'count'
                        results.append(NumberInfo(
                            value=cell,
                            numeric_value=numeric,
                            category=cat,
                            unit='',
                            context=f"표{ts.index+1} [{col}] 행{int(row_idx)+1}",
                            source='table',
                            table_index=ts.index,
                            row=int(row_idx),
                            col=col_idx,
                            document_id=document_id or ts.document_id,
                        ))

                for match in PATTERNS['percentage'].finditer(cell):
                    value_str = match.group(1).replace(',', '')
                    try:
                        numeric = float(value_str)
                    except ValueError:
                        numeric = None
                    results.append(NumberInfo(
                        value=match.group(0),
                        numeric_value=numeric,
                        category='percentage',
                        unit='%',
                        context=f"표{ts.index+1} [{col}] 행{int(row_idx)+1}",
                        source='table',
                        table_index=ts.index,
                        row=int(row_idx),
                        col=col_idx,
                        document_id=document_id or ts.document_id,
                    ))

    return results


TOTAL_ROW_PATTERN = re.compile(
    r'^(' + '|'.join(TOTAL_KEYWORDS) + r')$', re.IGNORECASE
)


def _is_total_row(df: pd.DataFrame, row_idx: int) -> bool:
    for col in df.columns[:3]:
        val = str(df.at[row_idx, col]).strip()
        if TOTAL_ROW_PATTERN.match(val):
            return True
    return False


def compute_column_sum(df: pd.DataFrame, col_name: str,
                       exclude_totals: bool = True) -> Optional[float]:
    total = 0.0
    count = 0
    for idx, val in df[col_name].items():
        if exclude_totals and _is_total_row(df, idx):
            continue
        cleaned = _normalize_number_str(str(val))
        if cleaned:
            try:
                total += float(cleaned)
                count += 1
            except ValueError:
                continue
    return total if count > 0 else None


def find_max_value_in_table(ts: TableSummary) -> Optional[dict]:
    """표에서 가장 큰 숫자값 찾기"""
    if ts.dataframe is None:
        return None

    max_val = None
    max_info = None

    for col in ts.numeric_columns + ts.money_columns:
        if col not in ts.dataframe.columns:
            continue
        for row_idx, val in ts.dataframe[col].items():
            cleaned = _normalize_number_str(str(val))
            if cleaned:
                try:
                    num = float(cleaned)
                    if max_val is None or num > max_val:
                        max_val = num
                        first_col = ts.dataframe.columns[0]
                        label = str(ts.dataframe.loc[row_idx, first_col]) if first_col != col else ''
                        max_info = {
                            'value': val,
                            'numeric_value': num,
                            'column': col,
                            'row_index': int(row_idx),
                            'label': label,
                            'table_index': ts.index,
                        }
                except ValueError:
                    continue

    return max_info


def filter_table_by_year(ts: TableSummary, year: int) -> Optional[pd.DataFrame]:
    """표에서 특정 연도 관련 데이터 필터링"""
    if ts.dataframe is None:
        return None

    df = ts.dataframe
    year_str = str(year)

    year_cols = [c for c in df.columns if year_str in str(c)]
    if year_cols:
        key_cols = [c for c in df.columns if c not in ts.numeric_columns + ts.money_columns]
        if not key_cols:
            key_cols = [df.columns[0]]
        return df[key_cols + year_cols]

    mask = df.apply(lambda row: any(year_str in str(v) for v in row), axis=1)
    if mask.any():
        return df[mask]

    return None


# =========================================================
# HWPX 표 그리드 파싱 (구 table_grid.py)
# =========================================================

def local_tag(tag: str) -> str:
    if '}' in tag:
        return tag.split('}')[-1]
    return tag


def get_cell_text(tc_elem: ET.Element) -> str:
    texts = []
    for elem in tc_elem.iter():
        if local_tag(elem.tag) == 't' and elem.text:
            texts.append(elem.text)
    result = ' '.join(texts).strip()
    return re.sub(r'\s+', ' ', result)


@dataclass
class CellMerge:
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1


@dataclass
class ParsedTableGrid:
    rows: list[list[str]] = field(default_factory=list)
    merges: list[CellMerge] = field(default_factory=list)
    covered: set[tuple[int, int]] = field(default_factory=set)

    @property
    def num_rows(self) -> int:
        return len(self.rows)

    @property
    def num_cols(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    def get_merge_at(self, row: int, col: int) -> CellMerge | None:
        for m in self.merges:
            if m.row == row and m.col == col:
                return m
        return None


def _collect_addressed_cells(
    tbl_elem: ET.Element,
) -> tuple[bool, list[tuple[int, int, int, int, ET.Element, str]], int, int]:
    """cellAddr 기반 셀 목록과 그리드 크기 반환."""
    row_cnt = int(tbl_elem.get('rowCnt', '0') or '0')
    col_cnt = int(tbl_elem.get('colCnt', '0') or '0')

    cells: list[tuple[int, int, int, int, ET.Element, str]] = []
    max_col = 0
    max_row = 0
    has_addr = False

    for tr_elem in tbl_elem:
        if local_tag(tr_elem.tag) not in ('tr', 'row'):
            continue
        for tc_elem in tr_elem:
            if local_tag(tc_elem.tag) not in ('tc', 'cell', 'td'):
                continue
            cell_text = get_cell_text(tc_elem)
            addr_elem = None
            span_elem = None
            for sub in tc_elem:
                st = local_tag(sub.tag)
                if st == 'cellAddr':
                    addr_elem = sub
                elif st == 'cellSpan':
                    span_elem = sub

            if addr_elem is not None:
                has_addr = True
                col = int(addr_elem.get('colAddr', '0'))
                row = int(addr_elem.get('rowAddr', '0'))
                cs = int(span_elem.get('colSpan', '1')) if span_elem is not None else 1
                rs = int(span_elem.get('rowSpan', '1')) if span_elem is not None else 1
                cells.append((row, col, cs, rs, tc_elem, cell_text))
                max_col = max(max_col, col + cs)
                max_row = max(max_row, row + rs)

    if has_addr and cells:
        if col_cnt > 0:
            max_col = max(max_col, col_cnt)
        if row_cnt > 0:
            max_row = max(max_row, row_cnt)

    return has_addr, cells, max_row, max_col


def build_element_grid(tbl_elem: ET.Element) -> list[list[tuple[ET.Element | None, str]]]:
    """편집용 — 각 셀의 (tc Element, 텍스트) 그리드. 병합 셀은 좌상단만 Element."""
    has_addr, cells, max_row, max_col = _collect_addressed_cells(tbl_elem)
    if not has_addr or not cells:
        return []

    grid: list[list[tuple[ET.Element | None, str]]] = [
        [(None, '') for _ in range(max_col)] for _ in range(max_row)
    ]
    for row_idx, col_idx, _cs, _rs, tc_elem, text in cells:
        if row_idx < max_row and col_idx < max_col:
            grid[row_idx][col_idx] = (tc_elem, text)
    return grid


def parse_table_grid(tbl_elem: ET.Element) -> ParsedTableGrid:
    row_cnt = int(tbl_elem.get('rowCnt', '0') or '0')
    col_cnt = int(tbl_elem.get('colCnt', '0') or '0')

    has_addr, cells, max_row, max_col = _collect_addressed_cells(tbl_elem)

    if has_addr and cells:
        grid = [[''] * max_col for _ in range(max_row)]
        merges: list[CellMerge] = []
        covered: set[tuple[int, int]] = set()

        for row_idx, col_idx, cs, rs, _tc, text in cells:
            if row_idx >= max_row or col_idx >= max_col:
                continue
            grid[row_idx][col_idx] = text
            if cs > 1 or rs > 1:
                merges.append(CellMerge(row_idx, col_idx, rs, cs))
            for r in range(row_idx, min(row_idx + rs, max_row)):
                for c in range(col_idx, min(col_idx + cs, max_col)):
                    if r == row_idx and c == col_idx:
                        continue
                    covered.add((r, c))
                    if not grid[r][c]:
                        grid[r][c] = text

        return ParsedTableGrid(rows=grid, merges=merges, covered=covered)

    return _parse_table_fallback(tbl_elem, row_cnt, col_cnt)


def _parse_table_fallback(tbl_elem: ET.Element, row_cnt: int, col_cnt: int) -> ParsedTableGrid:
    raw_cells: list[tuple[int, int, int, int, str]] = []
    fb_row_idx = 0
    for child in tbl_elem:
        if local_tag(child.tag) not in ('tr', 'row'):
            continue
        fb_col_idx = 0
        for cell_elem in child:
            if local_tag(cell_elem.tag) not in ('tc', 'cell', 'td'):
                continue
            text = get_cell_text(cell_elem)
            cs = int(cell_elem.get('colSpan', '1') or '1')
            rs = int(cell_elem.get('rowSpan', '1') or '1')
            raw_cells.append((fb_row_idx, fb_col_idx, cs, rs, text))
            fb_col_idx += cs
        fb_row_idx += 1

    if not raw_cells:
        return ParsedTableGrid()

    fb_max_row = max(r + rs for r, _, _, rs, _ in raw_cells)
    fb_max_col = max(col_cnt, max(c + cs for _, c, cs, _, _ in raw_cells))
    if row_cnt > 0:
        fb_max_row = max(fb_max_row, row_cnt)

    grid = [[''] * fb_max_col for _ in range(fb_max_row)]
    occupied = [[False] * fb_max_col for _ in range(fb_max_row)]
    merges: list[CellMerge] = []
    covered: set[tuple[int, int]] = set()

    row_cells: dict[int, list] = {}
    for r, c, cs, rs, text in raw_cells:
        row_cells.setdefault(r, []).append((c, cs, rs, text))

    for r_idx in sorted(row_cells.keys()):
        col_cursor = 0
        for _, cs, rs, text in row_cells[r_idx]:
            while col_cursor < fb_max_col and occupied[r_idx][col_cursor]:
                col_cursor += 1
            if col_cursor >= fb_max_col:
                break
            grid[r_idx][col_cursor] = text
            if cs > 1 or rs > 1:
                merges.append(CellMerge(r_idx, col_cursor, rs, cs))
            for dr in range(rs):
                for dc in range(cs):
                    rr, cc = r_idx + dr, col_cursor + dc
                    if rr < fb_max_row and cc < fb_max_col:
                        occupied[rr][cc] = True
                        if not (dr == 0 and dc == 0):
                            covered.add((rr, cc))
                        if not grid[rr][cc]:
                            grid[rr][cc] = text
            col_cursor += cs

    rows = [row for row in grid if any(str(cell).strip() for cell in row)]
    return ParsedTableGrid(rows=grid if grid else rows, merges=merges, covered=covered)


def is_inside_table(elem: ET.Element, root: ET.Element) -> bool:
    parent_map = {child: parent for parent in root.iter() for child in parent}
    current = elem
    while current in parent_map:
        current = parent_map[current]
        if local_tag(current.tag) in ('tbl', 'table'):
            return True
    return False
