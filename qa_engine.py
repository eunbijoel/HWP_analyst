"""
질의응답 엔진
- 표 데이터 기반 자동 계산 (엔티티+지표 교차 조회, 합계/비교 등)
- Rule-based 분석
- Ollama LLM 연결 (gemma4 기본)
"""

import re
import time
import requests
import pandas as pd
from typing import Optional
from table_extractor import (
    TableSummary, NumberInfo, BUDGET_KEYWORDS, TOTAL_KEYWORDS,
    compute_column_sum, find_max_value_in_table, filter_table_by_year,
)


SUM_KEYWORDS = ['합계', '합산', '총합', '합', '더해', '합쳐', '총', '전체 합', '다 더', 'sum', '합을']
SYSTEM_PROMPT = """당신은 한글(HWP) 문서 분석 전문가입니다.

## 핵심 규칙:
1. 반드시 제공된 문서 데이터에만 근거하여 답변하세요. 추측 금지.
2. 표 읽기: 행 헤더(첫 번째~두 번째 열)와 열 헤더(첫 번째 행)의 교차점에서 값을 찾으세요.
3. 단위 주의: [단위: 천원]이면 실제 금액 = 표 숫자 × 1,000원.
4. 근거 표시: "표 N의 'A' 행, 'B' 열" 형태로 출처를 명시하세요.
5. **사전 계산 결과가 제공되면 그 결과를 신뢰하고 활용하세요.** 사전 계산은 표 데이터를 프로그래밍으로 정확히 추출·계산한 것입니다.
6. 계산이 필요하면 과정을 단계별로 보여주세요.
7. 확실하지 않으면 "문서에서 명확히 확인되지 않음"이라고 표시하세요."""


def _parse_number(s: str) -> Optional[float]:
    """문자열에서 숫자 파싱"""
    if not s or s.strip() in ('-', '', '*자본잠식', '해당없음', '산출 불가'):
        return None
    cleaned = str(s).replace(',', '').replace(' ', '').strip()
    cleaned = re.sub(r'[천만백억조원%명개]', '', cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return None


class QAEngine:
    def __init__(self, paragraphs: list, table_summaries: list[TableSummary],
                 text_numbers: list[NumberInfo], table_numbers: list[NumberInfo]):
        self.paragraphs = paragraphs
        self.tables = table_summaries
        self.text_numbers = text_numbers
        self.table_numbers = table_numbers
        self.all_numbers = text_numbers + table_numbers

    def answer(self, question: str, use_llm: bool = False,
               model: str = "gemma4", ollama_url: str = "http://localhost:11434") -> dict:
        # 1) 표 데이터 기반 자동 계산
        pre_computed = self._pre_compute_analysis(question)

        # 2) Rule-based 답변
        rule_result = self._rule_based_answer(question)

        # 3) LLM 답변
        if use_llm:
            return self._llm_answer(question, model, ollama_url, rule_result, pre_computed)

        if pre_computed:
            return {
                'answer': pre_computed,
                'source': '표 데이터 자동 계산',
                'confidence': 'high',
            }

        return rule_result

    # =========================================================
    # 표 데이터 기반 자동 계산 (핵심 신규 기능)
    # =========================================================

    def _pre_compute_analysis(self, question: str) -> str:
        """질문에서 엔티티·지표를 추출하고, DataFrame에서 값을 조회·계산"""
        q = question.strip()

        # 질문에서 엔티티(기관명 등) 찾기
        entities = self._find_entities_in_question(q)
        # 질문에서 지표(매출액, 자본총계 등) 찾기
        metrics = self._find_metrics_in_question(q)
        # 합계 연산 요청 여부
        wants_sum = any(kw in q for kw in SUM_KEYWORDS)
        # 특정 연도 필터
        year_matches = re.findall(r'((?:19|20)\d{2})', q)
        target_years = [int(y) for y in year_matches] if year_matches else []

        results = []

        if entities and metrics:
            for entity in entities:
                for metric in metrics:
                    lookup = self._lookup_entity_metric(entity, metric, target_years)
                    if lookup:
                        results.append(lookup)

        if not results and entities:
            for entity in entities:
                lookup = self._lookup_entity_all(entity, target_years)
                if lookup:
                    results.append(lookup)

        if not results and metrics:
            for metric in metrics:
                lookup = self._lookup_metric_all(metric, target_years)
                if lookup:
                    results.append(lookup)

        if not results:
            return ""

        parts = ["[사전 계산 결과]"]
        for r in results:
            parts.append(r['description'])
            if r.get('values'):
                for v in r['values']:
                    parts.append(f"  - {v['label']}: {v['raw']}")
            if wants_sum and r.get('values'):
                nums = [v['numeric'] for v in r['values'] if v['numeric'] is not None]
                if nums:
                    total = sum(nums)
                    unit = r.get('unit', '')
                    unit_text = f" ({unit})" if unit else ""
                    parts.append(f"  → 합계: {total:,.0f}{unit_text}")
                    parts.append(f"  → 계산: {' + '.join(f'{n:,.0f}' for n in nums)} = {total:,.0f}")

        return '\n'.join(parts)

    def _find_entities_in_question(self, question: str) -> list:
        """질문에서 표 컬럼 헤더와 매칭되는 엔티티(기관명 등) 찾기"""
        found = []
        q_clean = question.replace(' ', '')
        skip_headers = {'순번', '구분', '열2', '열3', '열4', '열5', '열6', '열7', '열8',
                        '기관명 구분', '기관명구분', '연구개발비', '소계', '합계', '총계'}
        for ts in self.tables:
            if ts.dataframe is None:
                continue
            for col in ts.headers:
                col_str = str(col).strip()
                if len(col_str) < 2 or col_str in skip_headers:
                    continue
                col_clean = col_str.replace(' ', '')
                if col_clean in q_clean or col_str in question:
                    already = any(
                        col_clean == f.replace(' ', '') or col_str in f or f in col_str
                        for f in found
                    )
                    if not already:
                        found.append(col_str)
        return found

    def _find_metrics_in_question(self, question: str) -> list:
        """질문에서 지표 키워드 매칭"""
        q_clean = question.replace(' ', '')
        metric_keywords = [
            '전년도 매출액', '매출액 대비 연구개발비 비율', '연구개발비 비율', '연구개발비',
            '자본잠식 현황', '자본 총계', '자본총계', '자본금',
            '부채 비율', '부채비율', '유동 비율', '유동비율',
            '영업 이익', '영업이익', '이자 보상 비율', '이자보상비율',
            '매출액', '사업비', '예산', '상시 종업원 수', '주생산 품목',
        ]
        found = []
        for kw in metric_keywords:
            if kw.replace(' ', '') in q_clean and kw not in found:
                found.append(kw)
                break  # 가장 구체적인 것 하나만
        return found

    def _find_year_label_col(self, df: pd.DataFrame, label_cols: list) -> Optional[str]:
        """연도(2023, 2024 등)가 들어있는 레이블 컬럼 찾기"""
        for col in df.columns[:6]:
            if col in label_cols:
                continue
            values = df[col].astype(str).str.strip()
            year_count = sum(1 for v in values if re.match(r'^(19|20)\d{2}$', v))
            if year_count >= 3:
                return col
        return None

    def _get_label_columns(self, df: pd.DataFrame) -> list:
        """표에서 레이블(행 이름) 역할을 하는 컬럼 식별"""
        label_cols = []
        for col in df.columns[:4]:
            col_str = str(col)
            values = df[col].astype(str)
            numeric_count = sum(1 for v in values if _parse_number(v) is not None and len(v.strip()) > 2)
            if numeric_count < len(values) * 0.5:
                label_cols.append(col)
        return label_cols if label_cols else [df.columns[0]]

    def _lookup_entity_metric(self, entity: str, metric: str, target_years: list) -> Optional[dict]:
        """특정 엔티티의 특정 지표 값을 표에서 조회"""
        entity_clean = entity.replace(' ', '')
        metric_clean = metric.replace(' ', '')

        for ts in self.tables:
            if ts.dataframe is None:
                continue
            df = ts.dataframe

            entity_col = None
            for col in df.columns:
                if str(col).replace(' ', '') == entity_clean or entity in str(col):
                    entity_col = col
                    break
            if entity_col is None:
                continue

            label_cols = self._get_label_columns(df)
            best_rows = []
            best_score = 0
            for idx, row in df.iterrows():
                for lc in label_cols:
                    cell = str(row.get(lc, '')).replace(' ', '')
                    if len(cell) < 2:
                        continue
                    score = 0
                    if cell == metric_clean:
                        score = 3
                    elif cell.startswith(metric_clean):
                        score = 2
                    elif metric_clean in cell:
                        cell_core = re.sub(r'[\(\（].*[\)\）]', '', cell)
                        if cell_core == metric_clean or cell_core.startswith(metric_clean):
                            score = 2
                        elif len(cell) <= len(metric_clean) * 3:
                            score = 1
                    elif len(cell) >= 3 and cell in metric_clean:
                        score = 1
                    if score > 0:
                        if score > best_score:
                            best_rows = [(score, idx)]
                            best_score = score
                        elif score == best_score:
                            best_rows.append((score, idx))
                        break

            matching_rows = [idx for _, idx in best_rows]
            if not matching_rows:
                continue

            expanded_rows = self._expand_sub_rows(df, matching_rows, label_cols)

            year_col = self._find_year_label_col(df, label_cols)

            values = []
            for idx in expanded_rows:
                row = df.iloc[idx]
                raw_val = str(row.get(entity_col, '')).strip()
                numeric_val = _parse_number(raw_val)

                label_parts = []
                for lc in label_cols:
                    v = str(row.get(lc, '')).strip()
                    if v and len(v) < 40:
                        label_parts.append(v)
                if year_col and year_col not in label_cols:
                    yv = str(row.get(year_col, '')).strip()
                    if yv:
                        label_parts.append(yv)
                label = ' '.join(label_parts) if label_parts else f"행 {idx}"

                if raw_val and raw_val != '-' and raw_val.strip():
                    values.append({
                        'label': label,
                        'raw': raw_val,
                        'numeric': numeric_val,
                    })

            if target_years and values:
                filtered = [v for v in values if any(str(y) in v['label'] for y in target_years)]
                if filtered:
                    values = filtered

            if values:
                return {
                    'description': f"표 {ts.index+1}에서 [{entity}]의 [{metric}] 조회:",
                    'values': values,
                    'unit': ts.unit,
                    'table_index': ts.index,
                }
            elif matching_rows:
                return {
                    'description': f"표 {ts.index+1}에서 [{entity}]의 [{metric}]: 해당 데이터 없음 ('-')",
                    'values': [],
                    'unit': ts.unit,
                    'table_index': ts.index,
                }

        return None

    def _lookup_entity_all(self, entity: str, target_years: list) -> Optional[dict]:
        """특정 엔티티의 모든 데이터 조회"""
        entity_clean = entity.replace(' ', '')

        for ts in self.tables:
            if ts.dataframe is None:
                continue
            df = ts.dataframe

            entity_col = None
            for col in df.columns:
                if str(col).replace(' ', '') == entity_clean or entity in str(col):
                    entity_col = col
                    break
            if entity_col is None:
                continue

            label_cols = self._get_label_columns(df)
            values = []
            for idx, row in df.iterrows():
                raw_val = str(row.get(entity_col, '')).strip()
                if not raw_val or raw_val == '-':
                    continue
                label_parts = [str(row.get(lc, '')).strip() for lc in label_cols]
                label = ' | '.join(p for p in label_parts if p and len(p) < 40)
                values.append({
                    'label': label,
                    'raw': raw_val,
                    'numeric': _parse_number(raw_val),
                })

            if values:
                return {
                    'description': f"표 {ts.index+1}에서 [{entity}]의 전체 데이터:",
                    'values': values[:30],
                    'unit': ts.unit,
                    'table_index': ts.index,
                }

        return None

    def _lookup_metric_all(self, metric: str, target_years: list) -> Optional[dict]:
        """특정 지표의 모든 엔티티 데이터 조회"""
        metric_clean = metric.replace(' ', '')

        for ts in self.tables:
            if ts.dataframe is None:
                continue
            df = ts.dataframe
            label_cols = self._get_label_columns(df)

            matching_rows = []
            for idx, row in df.iterrows():
                for lc in label_cols:
                    cell = str(row.get(lc, '')).replace(' ', '')
                    if len(cell) >= 2 and metric_clean in cell:
                        matching_rows.append(idx)
                        break

            if not matching_rows:
                continue

            expanded_rows = self._expand_sub_rows(df, matching_rows, label_cols)

            data_cols = [c for c in df.columns if c not in label_cols]
            values = []
            for col in data_cols:
                for idx in expanded_rows:
                    row = df.iloc[idx]
                    raw_val = str(row.get(col, '')).strip()
                    if not raw_val or raw_val == '-':
                        continue
                    label_parts = [str(row.get(lc, '')).strip() for lc in label_cols]
                    label = f"{col} | {' '.join(p for p in label_parts if p and len(p) < 30)}"
                    values.append({
                        'label': label,
                        'raw': raw_val,
                        'numeric': _parse_number(raw_val),
                    })

            if values:
                return {
                    'description': f"표 {ts.index+1}에서 [{metric}] 관련 전체 데이터:",
                    'values': values[:40],
                    'unit': ts.unit,
                    'table_index': ts.index,
                }

        return None

    def _expand_sub_rows(self, df: pd.DataFrame, matching_rows: list, label_cols: list) -> list:
        """매칭된 행의 하위 행(연도별 데이터 등) 포함"""
        expanded = []
        for idx in matching_rows:
            expanded.append(idx)
            for next_idx in range(idx + 1, min(idx + 8, len(df))):
                row = df.iloc[next_idx]
                is_sub_row = True
                for lc in label_cols:
                    val = str(row.get(lc, '')).strip()
                    if not val:
                        continue
                    if re.match(r'^(19|20)\d{2}$', val):
                        continue
                    is_sub_row = False
                    break
                if is_sub_row:
                    expanded.append(next_idx)
                else:
                    break
        return sorted(set(expanded))

    # =========================================================
    # Rule-based 답변
    # =========================================================

    def _rule_based_answer(self, question: str) -> dict:
        q = question.lower().strip()

        if any(kw in q for kw in ['예산', '사업비']) and any(kw in q for kw in ['표', '찾아', '관련']):
            return self._find_budget_tables()
        if any(kw in q for kw in ['총 사업비', '총사업비', '전체 예산', '총 예산', '총예산']):
            return self._find_total_budget()
        if any(kw in q for kw in ['연차별', '연도별', '년도별']):
            return self._find_yearly_budget()
        if any(kw in q for kw in ['기관별', '부서별', '부문별']):
            return self._find_by_category('기관/부서')
        if '가장 큰' in q or '최대' in q or '제일 큰' in q or '최고' in q:
            return self._find_max_item()
        year_match = re.search(r'(20\d{2})\s*년', q)
        if year_match and ('예산' in q or '뽑아' in q or '데이터' in q or '만' in q):
            return self._filter_by_year(int(year_match.group(1)))
        if any(kw in q for kw in SUM_KEYWORDS):
            return self._compute_sums()
        if any(kw in q for kw in ['비율', '퍼센트', '%', '프로']):
            return self._find_percentages()
        if '표' in q and ('몇 개' in q or '몇개' in q or '목록' in q or '리스트' in q):
            return self._list_tables()
        return self._general_search(question)

    def _find_budget_tables(self) -> dict:
        budget_tables = []
        for ts in self.tables:
            is_budget = any(any(kw in str(col) for kw in BUDGET_KEYWORDS) for col in ts.headers)
            if not is_budget:
                is_budget = bool(ts.money_columns)
            if not is_budget and ts.caption:
                is_budget = any(kw in ts.caption for kw in BUDGET_KEYWORDS)
            if is_budget:
                budget_tables.append(ts)
        if not budget_tables:
            budget_tables = [ts for ts in self.tables if ts.numeric_columns]
        if not budget_tables:
            return {'answer': '문서에서 예산 관련 표를 찾지 못했습니다.', 'source': '전체 표 검색', 'confidence': 'low'}
        parts = [f"예산 관련 표 {len(budget_tables)}개:\n"]
        for ts in budget_tables:
            unit_info = f" [단위: {ts.unit}]" if ts.unit else ""
            parts.append(f"- 표 {ts.index+1}: {ts.num_rows}행 x {ts.num_cols}열{unit_info} (헤더: {', '.join(ts.headers[:5])})")
        return {'answer': '\n'.join(parts), 'source': '표 분석', 'tables': budget_tables, 'confidence': 'high'}

    def _find_total_budget(self) -> dict:
        for ni in self.all_numbers:
            if ni.category == 'money' and any(kw in ni.context for kw in ['총 사업비', '총사업비', '전체 예산']):
                return {'answer': f"총 사업비: {ni.value}", 'source': f"'{ni.context}'", 'confidence': 'high'}
        for ts in self.tables:
            if ts.has_total_row and ts.dataframe is not None:
                total_row = ts.dataframe.iloc[ts.total_row_index]
                vals = []
                for col in ts.money_columns + ts.numeric_columns:
                    if col in ts.dataframe.columns:
                        v = str(total_row.get(col, '')).strip()
                        if v:
                            vals.append(f"{col}: {v}")
                if vals:
                    return {'answer': f"표 {ts.index+1} 합계 행:\n" + '\n'.join(vals), 'source': f"표 {ts.index+1}", 'confidence': 'medium'}
        return {'answer': '총 사업비를 찾지 못했습니다.', 'source': '전체 검색', 'confidence': 'low'}

    def _find_yearly_budget(self) -> dict:
        results = []
        for ts in self.tables:
            if ts.year_columns or any('년' in str(c) or re.match(r'^20\d{2}$', str(c).strip()) for c in ts.headers):
                if ts.dataframe is not None:
                    results.append((ts.index, ts.dataframe.to_string(index=False), ts.unit))
        if results:
            parts = ["연차별 예산 관련 표:\n"]
            for idx, preview, unit in results:
                u = f" [단위: {unit}]" if unit else ""
                parts.append(f"[표 {idx+1}]{u}\n{preview}\n")
            return {'answer': '\n'.join(parts), 'source': f"표 {', '.join(str(r[0]+1) for r in results)}", 'confidence': 'high'}
        return {'answer': '연차별 예산을 찾지 못했습니다.', 'source': '전체 검색', 'confidence': 'low'}

    def _find_by_category(self, name: str) -> dict:
        for ts in self.tables:
            if ts.dataframe is not None and not ts.dataframe.empty and (ts.money_columns or ts.numeric_columns):
                u = f" [단위: {ts.unit}]" if ts.unit else ""
                return {'answer': f"{name}별 데이터 (표 {ts.index+1}){u}:\n\n{ts.dataframe.to_string(index=False)}", 'source': f"표 {ts.index+1}", 'confidence': 'medium'}
        return {'answer': f'{name}별 정보를 찾지 못했습니다.', 'source': '전체 검색', 'confidence': 'low'}

    def _find_max_item(self) -> dict:
        max_info = None
        for ts in self.tables:
            result = find_max_value_in_table(ts)
            if result and (max_info is None or result['numeric_value'] > max_info['numeric_value']):
                max_info = result
        if max_info:
            label = f" (항목: {max_info['label']})" if max_info['label'] else ""
            return {'answer': f"가장 큰 금액: {max_info['value']}{label}\n위치: 표 {max_info['table_index']+1}, '{max_info['column']}' 컬럼", 'source': f"표 {max_info['table_index']+1}", 'confidence': 'high'}
        return {'answer': '금액 정보를 찾지 못했습니다.', 'source': '전체 검색', 'confidence': 'low'}

    def _filter_by_year(self, year: int) -> dict:
        results = []
        for ts in self.tables:
            filtered = filter_table_by_year(ts, year)
            if filtered is not None and not filtered.empty:
                results.append((ts.index, filtered.to_string(index=False)))
        if results:
            parts = [f"{year}년 데이터:\n"]
            for idx, data in results:
                parts.append(f"[표 {idx+1}]\n{data}\n")
            return {'answer': '\n'.join(parts), 'source': f"표 {', '.join(str(r[0]+1) for r in results)}", 'confidence': 'high'}
        return {'answer': f'{year}년 데이터를 찾지 못했습니다.', 'source': '전체 검색', 'confidence': 'low'}

    def _compute_sums(self) -> dict:
        results = []
        for ts in self.tables:
            if ts.dataframe is None:
                continue
            for col in set(ts.numeric_columns + ts.money_columns):
                if col in ts.dataframe.columns:
                    total = compute_column_sum(ts.dataframe, col)
                    if total is not None:
                        results.append((ts.index, col, total, ts.unit))
        if results:
            parts = ["표별 숫자 합계:\n"]
            for idx, col, s, unit in results:
                u = f" ({unit})" if unit else ""
                parts.append(f"- 표 {idx+1} [{col}]: {s:,.0f}{u}")
            return {'answer': '\n'.join(parts), 'source': '표 합산', 'confidence': 'high'}
        return {'answer': '합계를 계산할 데이터를 찾지 못했습니다.', 'source': '전체 검색', 'confidence': 'low'}

    def _find_percentages(self) -> dict:
        pct = [n for n in self.all_numbers if n.category == 'percentage']
        if pct:
            parts = [f"비율/퍼센트 {len(pct)}개:\n"]
            for n in pct[:20]:
                parts.append(f"- {n.value} ({n.context})")
            return {'answer': '\n'.join(parts), 'source': '문서 검색', 'confidence': 'high'}
        return {'answer': '비율 정보를 찾지 못했습니다.', 'source': '전체 검색', 'confidence': 'low'}

    def _list_tables(self) -> dict:
        if not self.tables:
            return {'answer': '표가 없습니다.', 'source': '파싱 결과', 'confidence': 'high'}
        parts = [f"문서 내 표 {len(self.tables)}개:\n"]
        for ts in self.tables:
            info = f"- 표 {ts.index+1}: {ts.num_rows}행 x {ts.num_cols}열"
            if ts.caption:
                info += f" ({ts.caption})"
            if ts.unit:
                info += f" [단위: {ts.unit}]"
            parts.append(info)
        return {'answer': '\n'.join(parts), 'source': '파싱 결과', 'confidence': 'high'}

    def _general_search(self, question: str) -> dict:
        keywords = [k for k in re.findall(r'[가-힣a-zA-Z0-9]+', question) if len(k) > 1]
        paras = [p for p in self.paragraphs if any(kw in p for kw in keywords)]
        tbls = [ts for ts in self.tables if ts.dataframe is not None and any(kw in ts.dataframe.to_string() for kw in keywords)]
        parts = []
        if paras:
            parts.append(f"관련 문단 {len(paras)}개:")
            for p in paras[:5]:
                parts.append(f"- {p[:200]}")
        if tbls:
            parts.append(f"\n관련 표 {len(tbls)}개:")
            for ts in tbls:
                parts.append(f"- 표 {ts.index+1}: {', '.join(ts.headers[:5])}")
        if not parts:
            return {'answer': '관련 정보를 찾지 못했습니다.', 'source': '검색', 'confidence': 'low'}
        return {'answer': '\n'.join(parts), 'source': '키워드 검색', 'confidence': 'medium'}

    # =========================================================
    # LLM
    # =========================================================

    def _llm_answer(self, question: str, model: str, ollama_url: str,
                    rule_result: dict, pre_computed: str) -> dict:
        context = self._build_context(question)
        rule_hint = rule_result.get('answer', '')

        pre_section = ""
        if pre_computed:
            pre_section = f"\n## 사전 계산 결과 (프로그래밍으로 정확히 계산됨 — 이 결과를 신뢰하세요):\n{pre_computed}\n"

        prompt = f"""{SYSTEM_PROMPT}

## 문서에서 추출된 데이터:

{context}
{pre_section}
## Rule-based 사전 분석:
{rule_hint}

## 사용자 질문:
{question}

## 답변 (근거 표시 필수):"""

        start_time = time.time()

        try:
            response = requests.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_predict": 2048,
                        "num_ctx": 16384,
                    }
                },
                timeout=180,
            )
            elapsed = time.time() - start_time

            if response.status_code == 200:
                result = response.json()
                llm_answer = result.get('response', '').strip()
                prompt_tokens = result.get('prompt_eval_count', 0)
                completion_tokens = result.get('eval_count', 0)

                return {
                    'answer': llm_answer,
                    'source': f'LLM ({model}) + 문서 분석',
                    'confidence': 'llm',
                    'model': model,
                    'elapsed': round(elapsed, 1),
                    'prompt_tokens': prompt_tokens,
                    'completion_tokens': completion_tokens,
                }
            else:
                return {
                    'answer': f"LLM 오류 (HTTP {response.status_code}).\n\nRule-based:\n{rule_hint}",
                    'source': rule_result.get('source', ''),
                    'confidence': rule_result.get('confidence', 'low'),
                    'error': response.text[:200],
                    'elapsed': round(time.time() - start_time, 1),
                }
        except requests.ConnectionError:
            return {
                'answer': f"Ollama 미연결.\n\nRule-based:\n{rule_hint}",
                'source': rule_result.get('source', ''),
                'confidence': rule_result.get('confidence', 'low'),
                'error': 'Ollama 연결 실패',
                'elapsed': round(time.time() - start_time, 1),
            }
        except requests.Timeout:
            return {
                'answer': f"LLM 시간 초과.\n\nRule-based:\n{rule_hint}",
                'source': rule_result.get('source', ''),
                'confidence': rule_result.get('confidence', 'low'),
                'error': 'Timeout',
                'elapsed': round(time.time() - start_time, 1),
            }
        except Exception as e:
            return {
                'answer': f"LLM 오류: {e}\n\nRule-based:\n{rule_hint}",
                'source': rule_result.get('source', ''),
                'confidence': rule_result.get('confidence', 'low'),
                'error': str(e),
                'elapsed': round(time.time() - start_time, 1),
            }

    def _build_context(self, question: str) -> str:
        keywords = [k for k in re.findall(r'[가-힣a-zA-Z0-9]+', question) if len(k) > 1]
        relevant, other = self._rank_tables_by_relevance(keywords)

        parts = []
        for ts in relevant:
            parts.append(self._format_table_for_llm(ts))
        for ts in other:
            parts.append(self._format_table_for_llm(ts))

        rel_paras = [p for p in self.paragraphs if any(kw in p for kw in keywords)]
        if rel_paras:
            parts.append("\n---\n### 질문 관련 텍스트:")
            for p in rel_paras[:30]:
                parts.append(p)

        remaining = 16000 - sum(len(p) for p in parts)
        if remaining > 500:
            other_paras = [p for p in self.paragraphs if not any(kw in p for kw in keywords)]
            parts.append("\n---\n### 기타 텍스트:")
            for p in other_paras[:20]:
                if remaining <= 0:
                    break
                parts.append(p)
                remaining -= len(p)

        context = '\n'.join(parts)
        return context[:20000] if len(context) > 20000 else context

    def _rank_tables_by_relevance(self, keywords: list) -> tuple:
        scored = []
        for ts in self.tables:
            if ts.dataframe is None:
                scored.append((0, ts))
                continue
            score = 0
            text = ts.dataframe.to_string() + ' ' + ts.caption + ' ' + ' '.join(ts.headers)
            for kw in keywords:
                if kw in text:
                    score += 10
                for col in ts.headers:
                    if kw in str(col):
                        score += 5
            scored.append((score, ts))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [ts for s, ts in scored if s > 0], [ts for s, ts in scored if s == 0]

    def _format_table_for_llm(self, ts: TableSummary) -> str:
        if ts.dataframe is None:
            return ""
        header = f"\n### 표 {ts.index+1}"
        if ts.caption:
            header += f" - {ts.caption}"
        if ts.unit:
            header += f" [단위: {ts.unit}]"
        header += f" ({ts.num_rows}행 x {ts.num_cols}열)"

        md = self._dataframe_to_markdown(ts.dataframe)
        table_str = '\n'.join(md)

        meta = []
        if ts.has_total_row:
            meta.append(f"합계 행: {ts.total_row_index + 1}행")
        if ts.money_columns:
            meta.append(f"금액 컬럼: {', '.join(ts.money_columns)}")
        meta_str = f"\n  → {'; '.join(meta)}" if meta else ""

        return f"{header}\n{table_str}{meta_str}"

    def _dataframe_to_markdown(self, df: pd.DataFrame) -> list:
        headers = list(df.columns)
        col_widths = [max(len(str(h)), max((len(str(v)) for v in df[h]), default=0)) for h in headers]
        lines = [
            '| ' + ' | '.join(str(h).ljust(w) for h, w in zip(headers, col_widths)) + ' |',
            '| ' + ' | '.join('-' * w for w in col_widths) + ' |',
        ]
        for _, row in df.iterrows():
            lines.append('| ' + ' | '.join(str(row[h]).ljust(w) for h, w in zip(headers, col_widths)) + ' |')
        return lines


def check_ollama_status(ollama_url: str = "http://localhost:11434") -> dict:
    try:
        resp = requests.get(f"{ollama_url}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = resp.json().get('models', [])
            names = [m.get('name', '') for m in models]
            return {'status': 'running', 'models': names, 'has_gemma4': any('gemma4' in m for m in names)}
        return {'status': 'error', 'models': [], 'has_gemma4': False}
    except requests.ConnectionError:
        return {'status': 'not_running', 'models': [], 'has_gemma4': False}
    except Exception:
        return {'status': 'error', 'models': [], 'has_gemma4': False}
