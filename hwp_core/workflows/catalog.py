"""Top document tasks Korean researchers repeat — spec only, no retrieval logic."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class WorkflowSpec:
  id: str
  name_ko: str
  name_en: str
  required_input: list[str]
  expected_output: list[str]
  required_evidence: list[str]
  success_criteria: list[str]
  product: str = "B"  # A | B | both
  implemented: bool = False
  notes: str = ""

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)


# Top 10 — ordered by frequency in R&D proposal work (연구개발계획서·신청서)
TASK_CATALOG: list[WorkflowSpec] = [
  WorkflowSpec(
    id="fill_institution_info",
    name_ko="기관 정보 채우기",
    name_en="Fill institution information",
    required_input=[
      "편집 대상: 빈 기관 서식이 있는 HWPX (기관명·대표자·주소·연락처 등)",
      "참고 1개 이상: 기관 소개 HWPX 또는 기관정보 XLSX (사실 값 포함)",
      "활성 문서 = 채울 양식, 나머지 = 참고",
    ],
    expected_output=[
      "Evidence 기반 표 셀 제안 (write_table_cell)",
      "각 제안에 출처 문서·위치 표시",
      "근거 없는 칸은 비움 + skip 사유",
      "승인 전까지 pending (자동 적용 없음)",
    ],
    required_evidence=[
      "기관명·주소·대표자·전화·이메일 등은 참고 문서 표/문단에서만 복사",
      "동의어 라벨 허용 (주관기관, TEL, 전자우편 등)",
      "라벨·안내문·섹션 헤더는 값으로 쓰지 않음",
    ],
    success_criteria=[
      "기관명·대표자·주소·전화·이메일 중 참고에 있는 항목은 Evidence 제안 생성",
      "참고에 없는 사업자/법인등록번호는 제안하지 않음",
      "제안값마다 sources 비어 있지 않음",
      "수락 후 export HWPX에 값 유지, 무관 셀 변경 없음",
    ],
    product="B",
    implemented=True,
    notes="Internal FactFillTool via Completion Planner — not a user-facing chat workflow",
  ),
  WorkflowSpec(
    id="fill_participant_table",
    name_ko="참여연구원·인건비 표 채우기",
    name_en="Fill participant / labor table",
    required_input=[
      "인건비 현황표가 있는 HWPX",
      "인력 명단 XLSX (성명·직위·참여율·인건비)",
    ],
    expected_output=[
      "행별 write_table_cell 제안",
      "excel_cell 출처",
    ],
    required_evidence=[
      "엑셀 행과 표 행 1:1 매핑",
      "인건비·참여율은 엑셀 숫자만",
    ],
    success_criteria=[
      "표 빈 행 수 ≤ 엑셀 인원 수만큼 채움",
      "성명·직위·참여율·인건비 열이 출처와 일치",
    ],
    product="B",
    implemented=False,
    notes="DocFill 인건비 경로 존재; named workflow 미구현",
  ),
  WorkflowSpec(
    id="rewrite_project_necessity",
    name_ko="연구개발 필요성 작성·다듬기",
    name_en="Rewrite project necessity",
    required_input=[
      "빈 또는 초안 '연구개발 필요성' 섹션이 있는 HWPX",
      "선택: 참고 HWPX/PDF (배경·근거 문단)",
    ],
    expected_output=[
      "replace_paragraph 또는 insert_after 제안",
      "Context 또는 Evidence 초안",
    ],
    required_evidence=[
      "Evidence 우선: 참고에 목적/배경 문단",
      "없으면 현재 문서 맥락 Context (AI Draft 표시)",
    ],
    success_criteria=[
      "제안 본문이 '필요성/배경' 섹션에 정렬",
      "목표/기대효과 문단과 뒤바뀌지 않음",
    ],
    product="B",
    implemented=False,
  ),
  WorkflowSpec(
    id="rewrite_rd_objective",
    name_ko="연구개발 목표 작성·다듬기",
    name_en="Rewrite R&D objective",
    required_input=["목표 섹션 빈칸 HWPX", "선택: 참고 자료"],
    expected_output=["목표 문단 제안"],
    required_evidence=["참고의 목표 문장 또는 현재 문서 목표 맥락"],
    success_criteria=["rd_objective concept 정렬", "기대효과와 혼동 없음"],
    product="B",
    implemented=False,
  ),
  WorkflowSpec(
    id="summarize_expected_outcomes",
    name_ko="기대효과 요약·작성",
    name_en="Summarize expected outcomes",
    required_input=["기대효과 빈칸 HWPX", "선택: 참고·초안"],
    expected_output=["기대효과 문단 제안"],
    required_evidence=["참고 효과/성과 문단 또는 문서 내 효과 맥락"],
    success_criteria=["expected_effect concept", "목표 문단과 분리"],
    product="B",
    implemented=False,
  ),
  WorkflowSpec(
    id="compare_proposal_versions",
    name_ko="제안서 두 버전 비교",
    name_en="Compare two proposal versions",
    required_input=["비교할 HWPX/HWP 2개", "비교 관심 항목 (선택)"],
    expected_output=["차이 목록", "항목별 before/after", "누락·추가 필드"],
    required_evidence=["두 문서 파싱 결과", "표·문단 diff"],
    success_criteria=["주요 섹션 diff 식별", "숫자·기관명 변경 강조"],
    product="A",
    implemented=False,
    notes="Product A Q&A/분석 영역",
  ),
  WorkflowSpec(
    id="verify_budget_totals",
    name_ko="예산·인건비 합계 검증",
    name_en="Verify budget totals",
    required_input=["예산표 HWPX", "선택: 예실대비 XLSX"],
    expected_output=["합계 일치/불일치 리포트", "수정 제안 (선택)"],
    required_evidence=["표 내 숫자 파싱", "엑셀 대조"],
    success_criteria=["소계=항목합", "총사업비=세부합", "이중합산 없음"],
    product="A",
    implemented=False,
    notes="validation_api 존재; B 워크플로 미연결",
  ),
  WorkflowSpec(
    id="fill_pi_info",
    name_ko="연구책임자 정보 채우기",
    name_en="Fill PI information",
    required_input=[
      "연구책임자 블록 빈칸 HWPX (성명·전화·이메일·연구자번호)",
      "참고: 인사/기관 HWPX",
    ],
    expected_output=["PI 관련 셀 Evidence 제안"],
    required_evidence=["사람 이름·연락처는 참고에서만", "번호는 숫자 형식"],
    success_criteria=[
      "성명은 person 타입 통과",
      "연구개발기간 등 헤더가 성명으로 들어가지 않음",
    ],
    product="B",
    implemented=False,
    notes="기관 워크플로와 분리 예정",
  ),
  WorkflowSpec(
    id="fill_partner_orgs_table",
    name_ko="공동·참여기관 표 채우기",
    name_en="Fill partner organizations table",
    required_input=[
      "공동연구개발기관 표 HWPX",
      "참고: 협약서·기관목록",
    ],
    expected_output=["기관명·책임자·직위 열 제안"],
    required_evidence=["행별 기관명과 매칭되는 책임자/유형"],
    success_criteria=[
      "기관명 열은 organization",
      "책임자 열은 person",
      "기관유형 안내문이 값으로 들어가지 않음",
    ],
    product="B",
    implemented=False,
  ),
  WorkflowSpec(
    id="apply_review_feedback",
    name_ko="심사·내부검토 반영 수정",
    name_en="Apply review feedback",
    required_input=[
      "원본 HWPX",
      "검토 코멘트 (텍스트) 또는 수정 지시 HWPX",
    ],
    expected_output=["항목별 수정 제안", "변경 diff"],
    required_evidence=["코멘트와 매칭된 문단/셀"],
    success_criteria=["지시된 위치만 변경", "승인 후 반영"],
    product="B",
    implemented=False,
    notes="선택 리라이트로 부분 가능",
  ),
]


def get_task_catalog() -> list[dict[str, Any]]:
  return [t.to_dict() for t in TASK_CATALOG]


def get_workflow_spec(workflow_id: str) -> WorkflowSpec | None:
  for t in TASK_CATALOG:
    if t.id == workflow_id:
      return t
  return None
