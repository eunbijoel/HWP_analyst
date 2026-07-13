질문에서 엔티티와 지표를 추출하세요.

규칙:
- entities: 질문에 직접 언급된 기관명/회사명만. 반드시 아래 헤더 목록에 있는 이름만 사용.
- metrics: 질문에 직접 언급된 지표만. 질문에 없는 지표는 절대 추가하지 마세요.
- intent: 반드시 하나만 선택 (lookup, sum, max, filter, compare, summary 중 하나)
- years: 질문에 명시된 연도 숫자만. "1년차"는 연도가 아닙니다.
- 이전 대화에서 언급된 엔티티/지표가 현재 질문에서 생략("거기", "그것", "앞에서 말한")되었으면 이전 대화에서 찾아 포함하세요.

헤더 목록: {header_names}
지표 목록: {metric_list}
{history_section}
질문: {question}

JSON만 출력:
{"intent": "lookup", "entities": [], "metrics": [], "years": []}
