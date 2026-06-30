"""
표 추출 및 숫자 분석 모듈
- 표를 pandas DataFrame으로 변환
- 숫자/금액/비율/연도/기간 자동 탐지
- 표별 요약 정보 생성
"""

import re
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


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

        df, unit_from_row = _rows_to_dataframe(rows)

        unit = raw_table.get('unit', '') or unit_from_row

        summary = TableSummary(
            index=idx,
            caption=raw_table.get('caption', ''),
            unit=unit,
            num_rows=df.shape[0] if df is not None else len(rows),
            num_cols=df.shape[1] if df is not None else (max(len(r) for r in rows) if rows else 0),
            document_id=document_id,
        )

        summary.dataframe = df
        summary.headers = list(df.columns) if df is not None else []

        if df is not None and not df.empty:
            summary.numeric_columns = _find_numeric_columns(df)
            summary.money_columns = _find_money_columns(df)
            summary.year_columns = _find_year_columns(df)
            summary.has_total_row, summary.total_row_index = _find_total_row(df)
            summary.preview = df.head(5).to_string(index=False)

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


def _rows_to_dataframe(rows: list) -> tuple[Optional[pd.DataFrame], str]:
    """표 행 데이터를 DataFrame으로 변환. (df, unit_from_row) 반환"""
    if not rows:
        return None, ""

    unit_from_row = ""

    max_cols = max(len(r) for r in rows)
    normalized = []
    for r in rows:
        padded = r + [''] * (max_cols - len(r))
        normalized.append(padded)

    # 첫 행이 단위 정보만 담고 있으면 건너뛰기
    header_start = 0
    is_unit, unit_val = _is_unit_row(normalized[0])
    if is_unit and len(normalized) >= 3:
        unit_from_row = unit_val
        header_start = 1

    if len(normalized) - header_start >= 2:
        headers = normalized[header_start]
        seen = {}
        unique_headers = []
        for h in headers:
            h = str(h).strip() if h else ''
            if not h:
                h = f'열{len(unique_headers)+1}'
            if h in seen:
                seen[h] += 1
                h = f'{h}_{seen[h]}'
            else:
                seen[h] = 0
            unique_headers.append(h)

        data = normalized[header_start + 1:]
        df = pd.DataFrame(data, columns=unique_headers)
    else:
        df = pd.DataFrame(normalized)

    return df, unit_from_row


def _find_numeric_columns(df: pd.DataFrame) -> list:
    """숫자가 주로 들어있는 컬럼 찾기"""
    numeric_cols = []
    for col in df.columns:
        values = df[col].astype(str)
        numeric_count = 0
        for v in values:
            cleaned = v.replace(',', '').replace(' ', '').strip()
            cleaned = re.sub(r'[원천만백억조%]', '', cleaned)
            if cleaned and _is_number(cleaned):
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


def _is_number(s: str) -> bool:
    """문자열이 숫자인지 확인"""
    try:
        s = s.replace(',', '').strip()
        if s.endswith('%'):
            s = s[:-1]
        float(s)
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


def compute_column_sum(df: pd.DataFrame, col_name: str) -> Optional[float]:
    """컬럼의 숫자 합계 계산"""
    total = 0.0
    count = 0
    for val in df[col_name]:
        cleaned = str(val).replace(',', '').strip()
        cleaned = re.sub(r'[원천만백억조]', '', cleaned)
        if _is_number(cleaned):
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
            cleaned = str(val).replace(',', '').strip()
            cleaned = re.sub(r'[원천만백억조]', '', cleaned)
            if _is_number(cleaned):
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
