"""
Windows 한/글 연동 에이전트 (Phase 3 스텁)

Linux 서버의 Streamlit 앱은 HWPX ZIP 직접 편집 방식을 사용합니다.
Windows PC에서 한/글 프로그램을 직접 조종하려면 이 에이전트를 별도 실행하세요.

## 아키텍처
```
[Streamlit 서버] <--HTTP/REST--> [Windows Agent] <--COM/API--> [한/글]
```

## 설치 (Windows)
```
pip install pywin32 flask
```

## 실행
```
python hwp_bridge.py --port 8787
```

한/글에서 문서를 연 뒤, 브라우저/앱에서 에이전트 REST API로 편집 명령을 전송합니다.
"""

from __future__ import annotations

import argparse
import json
import sys

# Windows 전용 — Linux에서는 import 실패를 허용
try:
    import win32com.client  # type: ignore
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    from flask import Flask, jsonify, request
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def get_hwp_application():
    """한/글 COM 객체 반환 (Windows + 한/글 설치 필요)."""
    if not HAS_WIN32:
        raise RuntimeError('pywin32 필요 (Windows 전용)')
    return win32com.client.gencache.EnsureDispatch('HWPFrame.HwpObject')


def detect_open_document(hwp) -> dict:
    """현재 열린 한/글 문서 정보."""
    try:
        hwp.XHwpWindows.Item(0).Visible = True
        doc = hwp.HParameterSet.HFileOpenSave
        return {
            'detected': True,
            'path': getattr(doc, 'FileName', '') or '',
            'message': '한/글 문서가 감지되었습니다.',
        }
    except Exception as e:
        return {'detected': False, 'error': str(e)}


def insert_text_at_cursor(hwp, text: str) -> dict:
    """커서 위치에 텍스트 삽입."""
    try:
        act = hwp.CreateAction('InsertText')
        pset = act.CreateSet()
        act.GetDefault(pset)
        pset.SetItem('Text', text)
        act.Execute(pset)
        return {'ok': True, 'inserted_chars': len(text)}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def replace_selection(hwp, new_text: str) -> dict:
    """선택 영역을 새 텍스트로 교체."""
    try:
        hwp.HAction.GetDefault('InsertText', hwp.HParameterSet.HInsertText.HSet)
        hwp.HParameterSet.HInsertText.Text = new_text
        hwp.HAction.Execute('InsertText', hwp.HParameterSet.HInsertText.HSet)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def create_app():
    if not HAS_FLASK:
        raise RuntimeError('flask 필요: pip install flask')

    app = Flask(__name__)
    hwp = None

    @app.route('/health')
    def health():
        return jsonify({
            'status': 'ok',
            'platform': sys.platform,
            'hwp_com': HAS_WIN32,
        })

    @app.route('/detect', methods=['GET'])
    def detect():
        nonlocal hwp
        if not HAS_WIN32:
            return jsonify({'error': 'Windows + pywin32 필요'}), 501
        if hwp is None:
            hwp = get_hwp_application()
        return jsonify(detect_open_document(hwp))

    @app.route('/edit/insert', methods=['POST'])
    def edit_insert():
        nonlocal hwp
        data = request.get_json(force=True) or {}
        text = data.get('text', '')
        if not text:
            return jsonify({'error': 'text 필요'}), 400
        if hwp is None:
            hwp = get_hwp_application()
        return jsonify(insert_text_at_cursor(hwp, text))

    @app.route('/edit/replace_selection', methods=['POST'])
    def edit_replace():
        nonlocal hwp
        data = request.get_json(force=True) or {}
        text = data.get('text', '')
        if not text:
            return jsonify({'error': 'text 필요'}), 400
        if hwp is None:
            hwp = get_hwp_application()
        return jsonify(replace_selection(hwp, text))

    @app.route('/edit/batch', methods=['POST'])
    def edit_batch():
        """여러 편집 명령 일괄 적용 (에이전트형)."""
        data = request.get_json(force=True) or {}
        commands = data.get('commands', [])
        results = []
        for cmd in commands:
            action = cmd.get('action')
            if action == 'insert':
                results.append(insert_text_at_cursor(hwp, cmd.get('text', '')))
            elif action == 'replace_selection':
                results.append(replace_selection(hwp, cmd.get('text', '')))
            else:
                results.append({'ok': False, 'error': f'unknown action: {action}'})
        return jsonify({'results': results, 'applied': len(results)})

    return app


def main():
    parser = argparse.ArgumentParser(description='HWP Windows Bridge Agent')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()

    if not HAS_WIN32:
        print('경고: Windows/pywin32 환경이 아닙니다. REST 서버만 스텁으로 실행됩니다.')
    if not HAS_FLASK:
        print('flask 미설치: pip install flask')
        sys.exit(1)

    app = create_app()
    print(f'HWP Bridge Agent http://{args.host}:{args.port}')
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
