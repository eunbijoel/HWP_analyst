"""HWP ↔ HWPX 변환 헬퍼. hwpilot 우선, LibreOffice soffice fallback."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


def hwp_to_hwpx_bytes(file_bytes: bytes, filename: str) -> tuple[Optional[bytes], str]:
    """Returns (hwpx_bytes, note)."""
    try:
        from hwp_core.hwp_backends import hwpilot_convert_to_hwpx_ex
        converted, detail = hwpilot_convert_to_hwpx_ex(file_bytes, filename)
        if converted:
            return converted, "hwpilot"
        hwpilot_err = detail or "hwpilot 변환 실패"
    except Exception as e:
        hwpilot_err = str(e)

    out, note = _soffice_convert(file_bytes, filename, ".hwp", "hwpx")
    if out:
        return out, f"libreoffice ({note})"
    # LibreOffice는 보통 HWP→HWPX를 못 함. hwpilot이 본체.
    tip = (
        " hwpilot이 안 되면: cd \"HWP analysis/hwpilot\" && npm install"
    )
    return None, f"{hwpilot_err}; {note}.{tip}"


def hwpx_to_hwp_bytes(file_bytes: bytes, filename: str) -> tuple[Optional[bytes], str]:
    """편집본 HWPX → HWP 저장 시도 (LibreOffice). 실패하면 None."""
    # hwpilot convert는 HWP→HWPX 방향만 지원
    out, note = _soffice_convert(file_bytes, filename, ".hwpx", "hwp")
    if out:
        return out, f"libreoffice ({note})"
    return None, note


def _soffice_convert(
    file_bytes: bytes,
    filename: str,
    in_suffix: str,
    out_ext: str,
) -> tuple[Optional[bytes], str]:
    soffice = "/usr/bin/soffice"
    if not os.path.exists(soffice):
        return None, "LibreOffice(soffice) 없음"
    try:
        with tempfile.TemporaryDirectory(prefix="hwp_v2_conv_") as tmpdir:
            safe = Path(filename).stem + in_suffix
            in_path = os.path.join(tmpdir, safe)
            with open(in_path, "wb") as f:
                f.write(file_bytes)
            result = subprocess.run(
                [
                    soffice, "--headless", "--norestore", "--nolockcheck",
                    "--convert-to", out_ext, "--outdir", tmpdir, in_path,
                ],
                capture_output=True,
                timeout=90,
                text=True,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "")[:180]
                return None, f"soffice→{out_ext} 실패: {err or 'code '+str(result.returncode)}"

            base = Path(safe).stem
            out_path = os.path.join(tmpdir, f"{base}.{out_ext}")
            if not os.path.exists(out_path):
                cands = [f for f in os.listdir(tmpdir) if f.endswith(f".{out_ext}")]
                if not cands:
                    return None, f"soffice가 .{out_ext}를 만들지 않음"
                out_path = os.path.join(tmpdir, cands[0])
            with open(out_path, "rb") as f:
                data = f.read()
            if not data:
                return None, f".{out_ext} 파일이 비어 있음"
            return data, out_ext
    except subprocess.TimeoutExpired:
        return None, "soffice 시간 초과"
    except Exception as e:
        return None, f"soffice 예외: {e}"