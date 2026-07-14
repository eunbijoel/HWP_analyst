"""테스트용 최소 HWPX / XLSX 픽스처 생성."""

from __future__ import annotations

import io
import zipfile
from xml.etree.ElementTree import Element, SubElement, tostring


def _para(parent: Element, text: str) -> Element:
  p = SubElement(parent, "p")
  run = SubElement(p, "run")
  t = SubElement(run, "t")
  t.text = text
  return p


def _cell(tr: Element, row: int, col: int, text: str) -> Element:
  tc = SubElement(tr, "tc")
  addr = SubElement(tc, "cellAddr")
  addr.set("rowAddr", str(row))
  addr.set("colAddr", str(col))
  span = SubElement(tc, "cellSpan")
  span.set("rowSpan", "1")
  span.set("colSpan", "1")
  p = SubElement(tc, "p")
  run = SubElement(p, "run")
  t = SubElement(run, "t")
  t.text = text
  return tc


def make_minimal_hwpx(
  paragraphs: list[str] | None = None,
  tables: list[list[list[str]]] | None = None,
) -> bytes:
  """HWPXEditor가 읽고 쓸 수 있는 최소 ZIP."""
  paragraphs = paragraphs or []
  tables = tables or []

  root = Element("sec")
  for text in paragraphs:
    _para(root, text)

  for rows in tables:
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=0)
    tbl = SubElement(root, "tbl")
    tbl.set("rowCnt", str(n_rows))
    tbl.set("colCnt", str(n_cols))
    for ri, row in enumerate(rows):
      tr = SubElement(tbl, "tr")
      for ci in range(n_cols):
        val = row[ci] if ci < len(row) else ""
        _cell(tr, ri, ci, val)

  section_xml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="utf-8")

  buf = io.BytesIO()
  with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("mimetype", "application/hwp+zip")
    zf.writestr("Contents/section0.xml", section_xml)
    zf.writestr("Contents/header.xml", b'<?xml version="1.0"?><hh><charProperties/></hh>')
  return buf.getvalue()


def make_rd_plan_hwpx() -> bytes:
  """시나리오 A: 연구개발 목표·기대효과 빈 항목."""
  return make_minimal_hwpx([
    "연구개발계획서",
    "연구개발 필요성",
    "□",
    "연구개발 목표",
    "□",
    "기대효과",
    "□",
  ])


def make_company_ref_hwpx() -> bytes:
  return make_minimal_hwpx([
    "회사 소개",
    "우리 회사는 AI 기반 문서 분석 기술을 개발한다.",
    "연구개발 목표는 내부 예산·계획 문서의 숫자 오류를 자동으로 탐지하는 시스템을 구축하는 것이다.",
    "기대효과는 검토 시간을 단축하고 보고서 품질을 높이는 것이다. 파급효과로 타 기관 적용이 가능하다.",
    "추진 배경으로 공공 문서의 HWP 비중이 높은 점을 고려한다.",
  ])


def make_labor_form_hwpx() -> bytes:
  """시나리오 B: 인건비 현황표 (빈 데이터 행)."""
  return make_minimal_hwpx(
    paragraphs=["인건비 현황표"],
    tables=[[
      ["성명", "직급", "참여율", "현금 인건비"],
      ["", "", "", ""],
      ["", "", "", ""],
      ["합계", "", "", ""],
    ]],
  )


def make_staff_xlsx() -> bytes:
  from openpyxl import Workbook
  wb = Workbook()
  ws = wb.active
  ws.title = "인력"
  ws.append(["이름", "직위", "참여비율", "인건비(현금)"])
  ws.append(["홍길동", "책임연구원", "50", "30000000"])
  ws.append(["김영희", "선임연구원", "30", "18000000"])
  buf = io.BytesIO()
  wb.save(buf)
  return buf.getvalue()
