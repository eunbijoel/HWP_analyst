"""Concept-aware expected value types for DocFill validation (no retrieval changes)."""

from __future__ import annotations

import re
from typing import Optional

from .document_inspector import get_fill_resolver

# Expected value type per concept (ontology concepts are not extended here).
CONCEPT_EXPECTED_TYPE: dict[str, str] = {
  "org_name": "organization",
  "address": "address",
  "representative": "person",
  "pi_name": "person",
  "person_name": "person",
  "phone": "phone",
  "mobile": "mobile",
  "email": "email",
  "business_reg_no": "number",
  "corp_reg_no": "number",
  "position": "position",
  "participation_rate": "ratio",
  "labor_cost_cash": "money",
}

LABEL_TYPE_HINTS: list[tuple[str, str]] = [
  (r"메일|이메일|전자우편|e-?mail", "email"),
  (r"전화|휴대|연락처|팩스|tel", "phone"),
  (r"주소|소재지", "address"),
  (r"기관명|주관기관|수행기관|연구개발기관", "organization"),
  (r"성명|이름|대표자|책임자", "person"),
  (r"직위|직급", "position"),
  (r"사업자|법인|등록번호", "number"),
  (r"연월일|일자|기간", "date"),
  (r"연구자\s*번호|국가연구자", "number"),
  (r"공고|과제\s*번호", "number"),
  (r"유형", "text"),
]


def expected_value_type(concept_id: str, label: str = "") -> str:
  cid = (concept_id or "").strip()
  lab = (label or "").strip()
  if cid in CONCEPT_EXPECTED_TYPE:
    return CONCEPT_EXPECTED_TYPE[cid]
  for pat, vtype in LABEL_TYPE_HINTS:
    if lab and re.search(pat, lab, re.I):
      return vtype
  if cid == "form_blank" or not cid:
    return "text"
  return "text"


def _looks_like_field_label(text: str) -> bool:
  t = (text or "").strip()
  if not t:
    return False
  if re.search(
    r"(유형\s*\(|연월일|기입\s*필요|작성\s*필요|대학\s*,|출연연|중소기업\s*등|"
    r"선택하세요|해당\s*없음|\(예\s*:|예시\s*:)",
    t,
  ):
    return True
  if len(t) > 48:
    return False
  if len(t) <= 24:
    gr = get_fill_resolver().ground(t)
    if gr.grounded and gr.confidence >= 0.9 and gr.method in ("exact", "substring", "pattern"):
      return True
  if len(t) <= 16 and re.search(
    r"(성명|직위|직급|주소|전화|메일|우편|기관명|책임자|등록번호|연월일|유형)$", t,
  ):
    return True
  return False


def _universal_reject(value: str, concept_id: str, label: str) -> str:
  v = (value or "").strip()
  if not v:
    return "empty"
  if _looks_like_field_label(v):
    return "looks_like_field_label"
  if re.search(
    r"(유형|연월일|기입|작성\s*필요|대학\s*,|출연연|중소기업|등\s*\)|해당란)",
    v,
  ):
    return "instructional_or_header_phrase"
  if re.search(
    r"(연구개발\s*기간|연구개발기간|선정\s*방식|선정방식|품목\s*지정|품목지정|"
    r"공고\s*번호|과제\s*번호|중앙행정|전문기관|총\s*연구비)",
    v,
  ):
    return "section_or_sibling_header"
  # label alias match — avoid using field label as value
  from .workspace_retriever import _concept_label_aliases, _label_matches
  if _label_matches(v, _concept_label_aliases(concept_id, label)):
    return "matches_own_field_label"
  gr = get_fill_resolver().ground(v)
  if (
    gr.grounded
    and gr.confidence >= 0.9
    and gr.method in ("exact", "substring", "pattern")
    and len(v) <= 24
  ):
    return "grounds_as_ontology_label"
  return ""


def _type_reject(value_type: str, value: str, label: str) -> str:
  v = (value or "").strip()
  if value_type == "person":
    if not (2 <= len(v) <= 20):
      return "type_person:length"
    if re.search(
      r"(연월일|유형|기관|번호|전화|메일|주소|직위|제품|기술|"
      r"기간|과제|방식|선정|공고|품목|연구개발|주관|공동)",
      v,
    ):
      return "type_person:field_token"
    if re.search(r"\d{3,}", v):
      return "type_person:digits"
    return ""
  if value_type == "organization":
    if not (2 <= len(v) <= 80):
      return "type_organization:length"
    if "유형" in v or re.search(r"^(주관|공동|역할)$", v):
      return "type_organization:header"
    return ""
  if value_type == "email":
    if not ("@" in v and len(v) <= 120):
      return "type_email:shape"
    return ""
  if value_type == "phone":
    digits = re.sub(r"\D", "", v)
    if not (7 <= len(digits) <= 15):
      return "type_phone:shape"
    return ""
  if value_type == "mobile":
    digits = re.sub(r"\D", "", v)
    if not (10 <= len(digits) <= 15):
      return "type_mobile:shape"
    return ""
  if value_type == "address":
    if not (len(v) >= 6 and len(v) <= 200 and "유형" not in v):
      return "type_address:shape"
    return ""
  if value_type == "number":
    digits = re.sub(r"\D", "", v)
    if re.search(r"연구자\s*번호|국가연구자", label or ""):
      if len(digits) < 5 or len(v) > 24 or re.search(r"년|개월|기간", v):
        return "type_number:researcher_id"
      return ""
    if len(digits) < 3:
      return "type_number:too_few_digits"
    if re.search(r"년\s*\d*\s*개월", v):
      return "type_number:date_range"
    return ""
  if value_type == "date":
    if not re.search(r"\d{4}|\d{1,2}\s*월|연월일", v):
      return "type_date:shape"
    return ""
  if value_type == "position":
    if not (1 <= len(v) <= 40):
      return "type_position:length"
    if re.search(r"(주관|공동|역할|비고|연구개발|기간|방식|선정|품목)", v):
      return "type_position:header"
    return ""
  if value_type == "ratio":
    if not re.search(r"\d", v):
      return "type_ratio:no_digit"
    return ""
  if value_type == "money":
    if not re.search(r"\d", v):
      return "type_money:no_digit"
    return ""
  # text / unknown: reject obvious headers only
  if re.search(r"년\s*\d*\s*개월|\d{4}\s*[.\-]\s*\d{1,2}\s*[.\-]\s*\d{1,2}", v):
    return "type_text:date_range"
  if re.fullmatch(r"[가-힣\s]+", v) and len(v) <= 12:
    if re.search(r"(방식|지정|기간|선정|품목|주관|공동)", v):
      return "type_text:korean_header"
  return ""


def value_rejection_reason(concept_id: str, label: str, value: str) -> str:
  """Empty string = accepted. Concept-aware type validation."""
  vtype = expected_value_type(concept_id, label)
  reason = _universal_reject(value, concept_id, label)
  if reason:
    return reason
  reason = _type_reject(vtype, value, label)
  if reason:
    return reason
  return ""


def value_fits_type(concept_id: str, label: str, value: str) -> bool:
  return value_rejection_reason(concept_id, label, value) == ""


def rank_validate_candidates(
  raw_candidates: list[dict],
  *,
  concept_id: str,
  label: str,
) -> list[dict]:
  """Preserve retrieval order; assign rank; validate each candidate."""
  vtype = expected_value_type(concept_id, label)
  ranked: list[dict] = []
  seen: set[tuple] = set()
  rank = 0
  for c in raw_candidates:
    key = (c.get("source_document"), c.get("source_location"), c.get("value"))
    if key in seen:
      continue
    seen.add(key)
    rank += 1
    val = str(c.get("value") or "").strip()
    reason = value_rejection_reason(concept_id, label, val)
    ranked.append({
      "rank": rank,
      "value": val,
      "source_document": c.get("source_document") or "",
      "source_location": c.get("source_location") or "",
      "source_type": c.get("source_type") or "table_cell",
      "expected_value_type": vtype,
      "accepted": reason == "",
      "rejection_reason": reason,
    })
  return ranked


def pick_accepted_candidate(
  raw_candidates: list[dict],
  *,
  concept_id: str,
  label: str,
) -> tuple[str, list[dict], dict, list[dict]]:
  """First accepted candidate in retrieval order; never stop at first rejection.

  Returns: (value, sources, accepted_candidate_dict, full_ranked_list)
  """
  ranked = rank_validate_candidates(raw_candidates, concept_id=concept_id, label=label)
  rejected = [c for c in ranked if not c.get("accepted")]
  for c in ranked:
    if not c.get("accepted"):
      continue
    srcs = [{
      "document": c.get("source_document"),
      "source_type": c.get("source_type") or "table_cell",
      "location": c.get("source_location"),
      "concept_id": concept_id,
    }]
    return str(c.get("value") or ""), srcs, c, ranked
  return "", [], {}, ranked
