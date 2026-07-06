"""
질의응답 엔진
- 2-Stage LLM 아키텍처: Stage 1 (소형 모델 intent/entity 추출) + Stage 2 (대형 모델 해석)
- 표 데이터 기반 자동 계산 (엔티티+지표 교차 조회, 합계/비교 등)
- Rule-based 분석
- Ollama LLM 연결 (Stage 1: gemma3:4b, Stage 2: gemma4 기본)
- 다중 문서 지원
"""

import re
import json
import time
import requests
import pandas as pd
from typing import Optional
from .table_extractor import (
    TableSummary, NumberInfo, BUDGET_KEYWORDS, TOTAL_KEYWORDS,
    UNIT_MULTIPLIERS, _normalize_number_str,
    compute_column_sum, find_max_value_in_table, filter_table_by_year,
)


SUM_KEYWORDS = ['합계', '합산', '총합', '합', '더해', '합쳐', '총', '전체 합', '다 더', 'sum', '합을']
CHART_KEYWORDS = ['그래프', '차트', '시각화', '막대', '그려', '그림', '도표', 'chart', 'graph', '비교 그래프', '바 차트']
OVERVIEW_QUESTION = re.compile(
    r'알려|소개|요약|개요|무슨|어떤\s*내용|설명해|정리해|이\s*자료|이\s*문서|전체|개략|뭐에\s*관',
    re.I,
)
SYSTEM_PROMPT = """당신은 한글(HWP) 문서 분석 전문가입니다.

## 핵심 규칙:
1. 반드시 제공된 문서 데이터에만 근거하여 답변하세요. 추측 금지.
2. 표 읽기: 행 헤더(첫 번째~두 번째 열)와 열 헤더(첫 번째 행)의 교차점에서 값을 찾으세요.
3. 단위 주의: [단위: 천원]이면 실제 금액 = 표 숫자 × 1,000원.
4. 근거 표시: "표 N의 'A' 행, 'B' 열" 형태로 출처를 명시하세요.
5. **사전 계산 결과가 제공되면 그 결과를 신뢰하고 활용하세요.** 사전 계산은 표 데이터를 프로그래밍으로 정확히 추출·계산한 것입니다.
6. 계산이 필요하면 과정을 단계별로 보여주세요.
7. 확실하지 않으면 "문서에서 명확히 확인되지 않음"이라고 표시하세요."""


def _format_value_with_unit(raw_val: str, numeric_val: Optional[float], unit: str) -> str:
    """단위가 있으면 실제값을 병기. 예: '120,000 (천원 = 1억 2,000만원)'"""
    multiplier = UNIT_MULTIPLIERS.get(unit, 1)
    if numeric_val is None or multiplier <= 1:
        return raw_val

    actual = numeric_val * multiplier
    if actual >= 1_0000_0000:
        eok = int(actual // 1_0000_0000)
        remainder = int((actual % 1_0000_0000) // 10000)
        if remainder > 0:
            actual_str = f"{eok}억 {remainder:,}만원"
        else:
            actual_str = f"{eok}억원"
    elif actual >= 10000:
        man = int(actual // 10000)
        actual_str = f"{man:,}만원"
    else:
        actual_str = f"{actual:,.0f}원"

    return f"{raw_val} ({unit} = {actual_str})"


def _parse_number(s: str) -> Optional[float]:
    if not s or s.strip() in ('-', '', '*자본잠식', '해당없음', '산출 불가'):
        return None
    cleaned = _normalize_number_str(str(s))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


class QAEngine:
    def __init__(self, paragraphs: list = None, table_summaries: list[TableSummary] = None,
                 text_numbers: list[NumberInfo] = None, table_numbers: list[NumberInfo] = None,
                 documents: list[dict] = None):
        if documents:
            self.multi_doc = True
            self.documents = documents
            self.paragraphs = []
            self.tables = []
            self.text_numbers = []
            self.table_numbers = []
            for doc_data in documents:
                self.paragraphs.extend(doc_data.get('paragraphs', []))
                self.tables.extend(doc_data.get('tables', []))
                self.text_numbers.extend(doc_data.get('text_numbers', []))
                self.table_numbers.extend(doc_data.get('table_numbers', []))
        else:
            self.multi_doc = False
            self.documents = []
            self.paragraphs = paragraphs or []
            self.tables = table_summaries or []
            self.text_numbers = text_numbers or []
            self.table_numbers = table_numbers or []

        self.all_numbers = self.text_numbers + self.table_numbers

    def answer(self, question: str, use_llm: bool = False,
               model: str = "gemma4", ollama_url: str = "http://localhost:11434",
               stream: bool = False, stage1_model: str = "gemma3:4b",
               history: list = None) -> dict:
        # Stage 1: LLM 기반 intent/entity 추출
        stage1_result = None
        if use_llm:
            stage1_result = self._stage1_analyze(question, model=stage1_model,
                                                  ollama_url=ollama_url,
                                                  history=history)

        wants_chart = any(kw in question for kw in CHART_KEYWORDS)
        chart_data = None
        pre_computed = ""

        # 신뢰도 순서: 직접 매칭 → 규칙기반 → pandas codegen
        # Step 1: Stage1 엔티티 기반 직접 교차 조회 (가장 신뢰도 높음)
        if stage1_result and (stage1_result['entities'] or stage1_result['metrics']):
            pre_computed, chart_data = self._pre_compute_with_stage1(question, stage1_result)

        # Step 2: 키워드 기반 규칙 매칭
        if not pre_computed:
            pre_computed, chart_data = self._pre_compute_analysis(question)

        # Step 3: pandas 코드 생성 (복잡한 질문에 대한 최후 수단)
        if not pre_computed and use_llm:
            pandas_result = self._try_pandas_code_gen(question, model=stage1_model,
                                                       ollama_url=ollama_url)
            if pandas_result:
                pre_computed = pandas_result

        # Rule-based 답변
        if stage1_result and stage1_result.get('intent'):
            rule_result = self._rule_based_with_intent(question, stage1_result['intent'])
        else:
            rule_result = self._rule_based_answer(question)

        if chart_data is None and rule_result.get('chart_data'):
            chart_data = rule_result['chart_data']

        if not wants_chart:
            chart_data = None

        # Stage 2: LLM 해석
        if use_llm:
            if stream:
                result = self._llm_answer_stream(question, model, ollama_url,
                                                 rule_result, pre_computed,
                                                 history=history)
            else:
                result = self._llm_answer(question, model, ollama_url,
                                          rule_result, pre_computed,
                                          history=history)
            if chart_data:
                result['chart_data'] = chart_data
            return result

        if pre_computed:
            result = {
                'answer': pre_computed,
                'source': '표 데이터 자동 계산',
                'confidence': 'high',
            }
            if chart_data:
                result['chart_data'] = chart_data
            return result

        if chart_data:
            rule_result['chart_data'] = chart_data
        return rule_result

    # =========================================================
    # Stage 1: 소형 모델 의도/엔티티 추출
    # =========================================================

    def _format_history(self, history: list) -> str:
        if not history:
            return ""
        recent = history[-3:]
        lines = []
        for chat in recent:
            q = chat.get('question', '')
            a = chat.get('answer', '')
            if len(a) > 200:
                a = a[:200] + '...'
            lines.append(f"사용자: {q}")
            lines.append(f"답변: {a}")
        return '\n'.join(lines)

    def _stage1_analyze(self, question: str, model: str = "gemma3:4b",
                        ollama_url: str = "http://localhost:11434",
                        history: list = None) -> Optional[dict]:
        header_names = []
        for ts in self.tables:
            for h in ts.headers:
                h_str = str(h).strip()
                if len(h_str) >= 2 and not h_str.startswith('열'):
                    header_names.append(h_str)
        header_names = list(dict.fromkeys(header_names))[:40]

        metric_list = [
            '매출액', '영업이익', '자본총계', '자본금', '부채비율', '유동비율',
            '연구개발비', '사업비', '예산', '이자보상비율', '종업원수',
        ]

        history_section = ""
        if history:
            history_text = self._format_history(history)
            history_section = f"\n이전 대화:\n{history_text}\n"

        prompt = f"""질문에서 엔티티와 지표를 추출하세요.

규칙:
- entities: 질문에 직접 언급된 기관명/회사명만. 반드시 아래 헤더 목록에 있는 이름만 사용.
- metrics: 질문에 직접 언급된 지표만. 질문에 없는 지표는 절대 추가하지 마세요.
- intent: 반드시 하나만 선택 (lookup, sum, max, filter, compare, summary 중 하나)
- years: 질문에 명시된 연도 숫자만. "1년차"는 연도가 아닙니다.
- 이전 대화에서 언급된 엔티티/지표가 현재 질문에서 생략("거기", "그것", "앞에서 말한")되었으면 이전 대화에서 찾아 포함하세요.

헤더 목록: {header_names[:30]}
지표 목록: {', '.join(metric_list)}
{history_section}
질문: {question}

JSON만 출력:
{{"intent": "lookup", "entities": [], "metrics": [], "years": []}}"""

        try:
            response = requests.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 256,
                        "num_ctx": 4096,
                    }
                },
                timeout=15,
            )
            if response.status_code != 200:
                return None

            text = response.json().get('response', '').strip()
            json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            if not json_match:
                return None

            parsed = json.loads(json_match.group())

            raw_intent = str(parsed.get('intent', 'lookup'))
            valid_intents = {'lookup', 'sum', 'max', 'filter', 'compare', 'summary'}
            intent = 'lookup'
            for vi in raw_intent.replace('|', ',').replace(' ', '').split(','):
                if vi.strip() in valid_intents:
                    intent = vi.strip()
                    break

            result = {
                'intent': intent,
                'entities': parsed.get('entities', []),
                'metrics': parsed.get('metrics', []),
                'years': [],
            }

            if not isinstance(result['entities'], list):
                result['entities'] = []
            if not isinstance(result['metrics'], list):
                result['metrics'] = []

            raw_years = parsed.get('years', [])
            if isinstance(raw_years, list):
                for y in raw_years:
                    try:
                        yi = int(y)
                        if 1900 <= yi <= 2100:
                            result['years'].append(yi)
                    except (ValueError, TypeError):
                        pass

            result['entities'] = self._validate_stage1_entities(result['entities'])
            result['metrics'] = self._validate_stage1_metrics(result['metrics'], question)

            return result
        except Exception:
            return None

    def _validate_stage1_entities(self, entities: list) -> list:
        """Stage 1이 추출한 엔티티가 실제 표 헤더에 존재하는지 검증"""
        if not entities:
            return []

        valid_names = set()
        for ts in self.tables:
            for h in ts.headers:
                h_str = str(h).strip()
                if len(h_str) >= 2 and not h_str.startswith('열'):
                    valid_names.add(h_str)

        validated = []
        for entity in entities:
            if not entity or len(entity) < 2:
                continue
            if re.match(r'^\d+$', entity.strip()):
                continue
            if entity.startswith('표') and entity[1:].isdigit():
                continue
            entity_clean = entity.replace(' ', '')
            for name in valid_names:
                name_clean = name.replace(' ', '')
                if len(entity_clean) >= 3 and len(name_clean) >= 3:
                    if entity_clean == name_clean or entity_clean in name_clean or name_clean in entity_clean:
                        validated.append(name)
                        break
        return validated

    def _validate_stage1_metrics(self, metrics: list, question: str) -> list:
        """Stage 1이 추출한 지표가 질문에 실제로 언급되었는지 검증"""
        if not metrics:
            return []
        q_clean = question.replace(' ', '')
        validated = []
        for metric in metrics:
            metric_clean = metric.replace(' ', '')
            if metric_clean in q_clean:
                validated.append(metric)
            else:
                for keyword in metric_clean:
                    pass
                short_forms = [metric_clean[:3], metric_clean[:4]]
                if any(sf in q_clean for sf in short_forms if len(sf) >= 2):
                    validated.append(metric)
        return validated

    # =========================================================
    # 차트 데이터 생성
    # =========================================================

    def _make_chart_data(self, values: list, title: str, unit: str = "") -> Optional[dict]:
        items = [(v['label'][:20], v['numeric']) for v in values
                 if v.get('numeric') is not None]
        if len(items) < 2:
            return None
        items = items[:15]
        labels, nums = zip(*items)
        df = pd.DataFrame({'값': list(nums)}, index=list(labels))
        return {'data': df, 'title': title, 'unit': unit}

    # =========================================================
    # LLM Pandas 코드 생성
    # =========================================================

    def _build_table_schema(self, question: str) -> str:
        keywords = [k for k in re.findall(r'[가-힣a-zA-Z0-9]+', question) if len(k) > 1]
        relevant, other = self._rank_tables_by_relevance(keywords)
        ordered = (relevant + other)[:5]

        parts = []
        total_len = 0
        for ts in ordered:
            if ts.dataframe is None or ts.dataframe.empty:
                continue
            df = ts.dataframe
            unit_info = f" [단위: {ts.unit}]" if ts.unit else ""
            caption_info = f" - {ts.caption}" if ts.caption else ""
            header = f"tables[{ts.index}]: 표 {ts.index+1}{caption_info}{unit_info} ({df.shape[0]}행 x {df.shape[1]}열)"
            cols = f"  컬럼: {list(df.columns)}"
            sample_rows = []
            for i in range(min(3, len(df))):
                row_vals = [str(df.iloc[i, j])[:30] for j in range(len(df.columns))]
                sample_rows.append("    " + " | ".join(row_vals))
            sample = "  샘플:\n" + "\n".join(sample_rows) if sample_rows else ""
            block = f"{header}\n{cols}\n{sample}\n"
            if total_len + len(block) > 4000:
                break
            parts.append(block)
            total_len += len(block)

        return "\n".join(parts)

    def _generate_pandas_code(self, question: str, schema: str,
                              model: str, ollama_url: str) -> Optional[str]:
        prompt = f"""당신은 pandas 전문가입니다. DataFrame에서 질문에 답하는 Python 코드를 작성하세요.

## 사용 가능한 변수
- tables: dict[int, pd.DataFrame]  (키는 표 번호, 0부터 시작)
- pd: pandas 모듈

## 규칙
1. 결과를 반드시 `result` 변수에 문자열로 저장하세요.
2. 행/열 이름으로 찾을 때는 str.contains()로 부분 매칭하세요.
3. 숫자가 문자열이면: .str.replace(',','').astype(float) 로 변환하세요.
4. 코드만 출력하세요. 설명이나 마크다운 블록(```)은 쓰지 마세요.
5. 에러가 나지 않도록 .empty 체크, try/except를 사용하세요.

## DataFrame 스키마
{schema}

## 질문
{question}

## Python 코드:
"""
        try:
            response = requests.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 512,
                        "num_ctx": 8192,
                    }
                },
                timeout=30,
            )
            if response.status_code != 200:
                return None

            text = response.json().get('response', '').strip()
            code_match = re.search(r'```(?:python)?\s*\n?(.*?)```', text, re.DOTALL)
            if code_match:
                return code_match.group(1).strip()
            if 'result' in text and ('tables' in text or 'df' in text):
                return text.strip()
            return None
        except Exception:
            return None

    def _safe_execute_pandas(self, code: str, tables_dict: dict) -> Optional[str]:
        forbidden = ['import ', 'open(', 'exec(', 'eval(', '__', 'os.', 'sys.',
                      'subprocess', 'shutil', 'globals(', 'locals(', 'compile(']
        for f in forbidden:
            if f in code:
                return None

        safe_builtins = {
            'len': len, 'range': range, 'sum': sum, 'min': min, 'max': max,
            'round': round, 'str': str, 'int': int, 'float': float,
            'list': list, 'dict': dict, 'tuple': tuple, 'set': set,
            'sorted': sorted, 'enumerate': enumerate, 'zip': zip,
            'map': map, 'filter': filter, 'print': print,
            'isinstance': isinstance, 'type': type, 'abs': abs,
            'any': any, 'all': all, 'bool': bool, 'ValueError': ValueError,
            'KeyError': KeyError, 'IndexError': IndexError, 'TypeError': TypeError,
        }

        namespace = {
            '__builtins__': safe_builtins,
            'tables': tables_dict,
            'pd': pd,
            're': re,
        }

        try:
            exec(code, namespace)
            result = namespace.get('result')
            if result is not None:
                return str(result)
            return None
        except Exception:
            return None

    def _try_pandas_code_gen(self, question: str, model: str,
                             ollama_url: str) -> Optional[str]:
        if not self.tables:
            return None

        schema = self._build_table_schema(question)
        if not schema:
            return None

        code = self._generate_pandas_code(question, schema, model, ollama_url)
        if not code:
            return None

        tables_dict = {}
        for ts in self.tables:
            if ts.dataframe is not None:
                tables_dict[ts.index] = ts.dataframe

        if not tables_dict:
            return None

        result = self._safe_execute_pandas(code, tables_dict)
        if result and len(result) > 5:
            return f"[사전 계산 결과 (pandas 코드 실행)]\n{result}"

        return None

    # =========================================================
    # 표 데이터 기반 자동 계산 (fallback)
    # =========================================================

    def _pre_compute_with_stage1(self, question: str, stage1: dict) -> tuple:
        q = question.strip()
        entities = stage1.get('entities', [])
        metrics = stage1.get('metrics', [])
        target_years = []
        for y in stage1.get('years', []):
            try:
                target_years.append(int(y))
            except (ValueError, TypeError):
                pass
        wants_sum = stage1.get('intent') == 'sum' or any(kw in q for kw in SUM_KEYWORDS)

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
            return self._pre_compute_analysis(question)

        chart_data = None
        parts = ["[사전 계산 결과 (Stage 1 LLM 추출)]"]
        for r in results:
            parts.append(r['description'])
            r_unit = r.get('unit', '')
            if r.get('values'):
                for v in r['values']:
                    display = _format_value_with_unit(v['raw'], v.get('numeric'), r_unit)
                    parts.append(f"  - {v['label']}: {display}")
                if chart_data is None:
                    chart_data = self._make_chart_data(
                        r['values'], r.get('description', ''), r_unit)
                if wants_sum:
                    nums = [v['numeric'] for v in r['values'] if v['numeric'] is not None]
                    if nums:
                        total = sum(nums)
                        unit_text = f" ({r_unit})" if r_unit else ""
                        parts.append(f"  → 합계: {total:,.0f}{unit_text}")
                        multiplier = UNIT_MULTIPLIERS.get(r_unit, 1)
                        if multiplier > 1:
                            actual_total = total * multiplier
                            parts.append(f"  → 실제 합계: {_format_value_with_unit(f'{total:,.0f}', total, r_unit)}")

        return '\n'.join(parts), chart_data

    def _pre_compute_analysis(self, question: str) -> tuple:
        q = question.strip()

        entities = self._find_entities_in_question(q)
        metrics = self._find_metrics_in_question(q)
        wants_sum = any(kw in q for kw in SUM_KEYWORDS)
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
            return "", None

        chart_data = None
        parts = ["[사전 계산 결과]"]
        for r in results:
            parts.append(r['description'])
            r_unit = r.get('unit', '')
            if r.get('values'):
                for v in r['values']:
                    display = _format_value_with_unit(v['raw'], v.get('numeric'), r_unit)
                    parts.append(f"  - {v['label']}: {display}")
                if chart_data is None:
                    chart_data = self._make_chart_data(
                        r['values'], r.get('description', ''), r_unit)
            if wants_sum and r.get('values'):
                nums = [v['numeric'] for v in r['values'] if v['numeric'] is not None]
                if nums:
                    total = sum(nums)
                    unit_text = f" ({r_unit})" if r_unit else ""
                    parts.append(f"  → 합계: {total:,.0f}{unit_text}")
                    multiplier = UNIT_MULTIPLIERS.get(r_unit, 1)
                    if multiplier > 1:
                        parts.append(f"  → 실제 합계: {_format_value_with_unit(f'{total:,.0f}', total, r_unit)}")

        return '\n'.join(parts), chart_data

    def _find_entities_in_question(self, question: str) -> list:
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
                break
        return found

    def _find_year_label_col(self, df: pd.DataFrame, label_cols: list) -> Optional[str]:
        for col in df.columns[:6]:
            if col in label_cols:
                continue
            values = df[col].astype(str).str.strip()
            year_count = sum(1 for v in values if re.match(r'^(19|20)\d{2}$', v))
            if year_count >= 3:
                return col
        return None

    def _get_label_columns(self, df: pd.DataFrame) -> list:
        label_cols = []
        for col in df.columns[:4]:
            col_str = str(col)
            values = df[col].astype(str)
            numeric_count = sum(1 for v in values if _parse_number(v) is not None and len(v.strip()) > 2)
            if numeric_count < len(values) * 0.5:
                label_cols.append(col)
        return label_cols if label_cols else [df.columns[0]]

    def _lookup_entity_metric(self, entity: str, metric: str, target_years: list) -> Optional[dict]:
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

            doc_tag = f" [{ts.document_id}]" if ts.document_id and self.multi_doc else ""
            if values:
                return {
                    'description': f"표 {ts.index+1}{doc_tag}에서 [{entity}]의 [{metric}] 조회:",
                    'values': values,
                    'unit': ts.unit,
                    'table_index': ts.index,
                }
            elif matching_rows:
                return {
                    'description': f"표 {ts.index+1}{doc_tag}에서 [{entity}]의 [{metric}]: 해당 데이터 없음 ('-')",
                    'values': [],
                    'unit': ts.unit,
                    'table_index': ts.index,
                }

        return None

    def _lookup_entity_all(self, entity: str, target_years: list) -> Optional[dict]:
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
                doc_tag = f" [{ts.document_id}]" if ts.document_id and self.multi_doc else ""
                return {
                    'description': f"표 {ts.index+1}{doc_tag}에서 [{entity}]의 전체 데이터:",
                    'values': values[:30],
                    'unit': ts.unit,
                    'table_index': ts.index,
                }

        return None

    def _lookup_metric_all(self, metric: str, target_years: list) -> Optional[dict]:
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
                doc_tag = f" [{ts.document_id}]" if ts.document_id and self.multi_doc else ""
                return {
                    'description': f"표 {ts.index+1}{doc_tag}에서 [{metric}] 관련 전체 데이터:",
                    'values': values[:40],
                    'unit': ts.unit,
                    'table_index': ts.index,
                }

        return None

    def _expand_sub_rows(self, df: pd.DataFrame, matching_rows: list, label_cols: list) -> list:
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

    def _rule_based_with_intent(self, question: str, intent: str) -> dict:
        intent_map = {
            'sum': self._compute_sums,
            'max': self._find_max_item,
            'summary': self._list_tables,
        }

        if intent == 'filter':
            year_match = re.search(r'(20\d{2})', question)
            if year_match:
                return self._filter_by_year(int(year_match.group(1)))

        if intent == 'compare':
            return self._general_search(question)

        if intent in intent_map:
            return intent_map[intent]()

        return self._rule_based_answer(question)

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
            doc_tag = f" [{ts.document_id}]" if ts.document_id and self.multi_doc else ""
            parts.append(f"- 표 {ts.index+1}{doc_tag}: {ts.num_rows}행 x {ts.num_cols}열{unit_info} (헤더: {', '.join(ts.headers[:5])})")
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
        all_items = []
        for ts in self.tables:
            result = find_max_value_in_table(ts)
            if result:
                all_items.append(result)
                if max_info is None or result['numeric_value'] > max_info['numeric_value']:
                    max_info = result
        if max_info:
            label = f" (항목: {max_info['label']})" if max_info['label'] else ""
            answer_result = {'answer': f"가장 큰 금액: {max_info['value']}{label}\n위치: 표 {max_info['table_index']+1}, '{max_info['column']}' 컬럼", 'source': f"표 {max_info['table_index']+1}", 'confidence': 'high'}
            if len(all_items) >= 2:
                chart_values = [{'label': r.get('label', f"표{r['table_index']+1}") or f"표{r['table_index']+1}",
                                 'numeric': r['numeric_value']} for r in
                                sorted(all_items, key=lambda x: x['numeric_value'], reverse=True)]
                chart = self._make_chart_data(chart_values, '표별 최댓값 비교')
                if chart:
                    answer_result['chart_data'] = chart
            return answer_result
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
            chart_values = []
            for idx, col, s, unit in results:
                u = f" ({unit})" if unit else ""
                parts.append(f"- 표 {idx+1} [{col}]: {s:,.0f}{u}")
                chart_values.append({'label': f"표{idx+1} {col}", 'numeric': s})
            result = {'answer': '\n'.join(parts), 'source': '표 합산', 'confidence': 'high'}
            chart = self._make_chart_data(chart_values, '표별 합계', results[0][3] if results else '')
            if chart:
                result['chart_data'] = chart
            return result
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
            if ts.document_id and self.multi_doc:
                info += f" [{ts.document_id}]"
            if ts.caption:
                info += f" ({ts.caption})"
            if ts.unit:
                info += f" [단위: {ts.unit}]"
            parts.append(info)
        return {'answer': '\n'.join(parts), 'source': '파싱 결과', 'confidence': 'high'}

    def _general_search(self, question: str) -> dict:
        keywords = [k for k in re.findall(r'[가-힣a-zA-Z0-9]+', question) if len(k) > 1]
        paras = [p for p in self.paragraphs if any(kw in p for kw in keywords)]
        tbls = [ts for ts in self.tables if ts.dataframe is not None
                and any(kw in (ts.caption + ' ' + ' '.join(str(h) for h in ts.headers))
                        for kw in keywords)]
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
    # LLM (Stage 2)
    # =========================================================

    def _llm_answer(self, question: str, model: str, ollama_url: str,
                    rule_result: dict, pre_computed: str,
                    history: list = None) -> dict:
        context = self._build_context(question)
        rule_hint = rule_result.get('answer', '')

        pre_section = ""
        if pre_computed:
            pre_section = f"\n## 사전 계산 결과 (프로그래밍으로 정확히 계산됨 — 이 결과를 신뢰하세요):\n{pre_computed}\n"

        history_section = ""
        if history:
            history_text = self._format_history(history)
            history_section = f"\n## 이전 대화 (맥락 참고용):\n{history_text}\n"

        prompt = f"""{SYSTEM_PROMPT}

## 문서에서 추출된 데이터:

{context}
{pre_section}
## Rule-based 사전 분석:
{rule_hint}
{history_section}
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
                        "num_ctx": 32768,
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

    def _llm_answer_stream(self, question: str, model: str, ollama_url: str,
                           rule_result: dict, pre_computed: str,
                           history: list = None) -> dict:
        context = self._build_context(question)
        rule_hint = rule_result.get('answer', '')

        pre_section = ""
        if pre_computed:
            pre_section = f"\n## 사전 계산 결과 (프로그래밍으로 정확히 계산됨 — 이 결과를 신뢰하세요):\n{pre_computed}\n"

        history_section = ""
        if history:
            history_text = self._format_history(history)
            history_section = f"\n## 이전 대화 (맥락 참고용):\n{history_text}\n"

        prompt = f"""{SYSTEM_PROMPT}

## 문서에서 추출된 데이터:

{context}
{pre_section}
## Rule-based 사전 분석:
{rule_hint}
{history_section}

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
                    "stream": True,
                    "options": {
                        "temperature": 0.2,
                        "num_predict": 2048,
                        "num_ctx": 32768,
                    }
                },
                timeout=180,
                stream=True,
            )

            if response.status_code != 200:
                return {
                    'answer': f"LLM 오류 (HTTP {response.status_code}).\n\nRule-based:\n{rule_hint}",
                    'source': rule_result.get('source', ''),
                    'confidence': rule_result.get('confidence', 'low'),
                    'error': response.text[:200],
                    'elapsed': round(time.time() - start_time, 1),
                }

            def token_generator():
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        token = chunk.get('response', '')
                        if token:
                            yield token
                    except json.JSONDecodeError:
                        continue

            return {
                'answer_stream': token_generator(),
                'source': f'LLM ({model}) + 문서 분석',
                'confidence': 'llm',
                'model': model,
                'start_time': start_time,
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
        max_context = 8000
        max_per_table = 3000

        if not self.paragraphs and not self.tables:
            return "(문서에서 추출된 문단·표가 없습니다. 파싱 결과를 확인하세요.)"

        if OVERVIEW_QUESTION.search(question):
            return self._build_overview_context(max_context)

        keywords = [k for k in re.findall(r'[가-힣a-zA-Z0-9]+', question) if len(k) > 1]
        relevant, other = self._rank_tables_by_relevance(keywords)

        parts = []
        total = 0
        for ts in relevant:
            formatted = self._format_table_for_llm(ts)
            if len(formatted) > max_per_table:
                formatted = formatted[:max_per_table] + '\n... (이하 생략)'
            if total + len(formatted) > max_context:
                break
            parts.append(formatted)
            total += len(formatted)

        if total < max_context * 0.5:
            for ts in other[:3]:
                formatted = self._format_table_for_llm(ts)
                if len(formatted) > max_per_table:
                    formatted = formatted[:max_per_table] + '\n... (이하 생략)'
                if total + len(formatted) > max_context:
                    break
                parts.append(formatted)
                total += len(formatted)

        rel_paras = [p for p in self.paragraphs if any(kw in p for kw in keywords)]
        if rel_paras and total < max_context:
            parts.append("\n---\n### 관련 텍스트:")
            for p in rel_paras[:10]:
                if total + len(p) > max_context:
                    break
                parts.append(p)
                total += len(p)

        context = '\n'.join(parts)
        if not context.strip() or len(context.strip()) < 80:
            return self._build_overview_context(max_context)
        return context

    def _build_overview_context(self, max_context: int = 8000) -> str:
        """개요/소개 질문 또는 키워드 미매칭 시 문서 앞부분·표 요약 제공."""
        parts = [f"### 문서 개요 (문단 {len(self.paragraphs)}개, 표 {len(self.tables)}개)"]
        total = len(parts[0])

        if self.paragraphs:
            parts.append("\n### 본문 (앞부분):")
            for p in self.paragraphs[:25]:
                chunk = p[:600]
                if total + len(chunk) > max_context:
                    break
                parts.append(f"- {chunk}")
                total += len(chunk)

        max_per_table = 2500
        for ts in self.tables[:8]:
            formatted = self._format_table_for_llm(ts)
            if len(formatted) > max_per_table:
                formatted = formatted[:max_per_table] + '\n... (이하 생략)'
            if total + len(formatted) > max_context:
                break
            parts.append(formatted)
            total += len(formatted)

        return '\n'.join(parts)

    def _rank_tables_by_relevance(self, keywords: list) -> tuple:
        scored = []
        for ts in self.tables:
            if ts.dataframe is None:
                scored.append((0, ts))
                continue
            score = 0
            searchable = ts.caption + ' ' + ' '.join(str(h) for h in ts.headers)
            if ts.dataframe is not None and len(ts.dataframe.columns) > 0:
                label_cols = ts.dataframe.columns[:2]
                for lc in label_cols:
                    searchable += ' ' + ' '.join(ts.dataframe[lc].astype(str).tolist()[:20])
            for kw in keywords:
                if kw in searchable:
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
        if ts.document_id and self.multi_doc:
            header += f" [{ts.document_id}]"
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
