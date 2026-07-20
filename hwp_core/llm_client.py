"""
Ollama LLM 통합 클라이언트
모든 Ollama HTTP 호출을 하나의 모듈로 통합.
"""

import json
import re
from typing import Optional, Generator

import requests


DEFAULT_OLLAMA_URL = "http://localhost:11434"


def check_ollama_status(ollama_url: str = DEFAULT_OLLAMA_URL) -> dict:
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


def generate(
    prompt: str,
    model: str,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    *,
    stream: bool = False,
    temperature: float = 0.2,
    num_predict: int = 2048,
    num_ctx: int = 32768,
    timeout: int = 180,
    format: Optional[str] = None,
) -> dict:
    """Ollama /api/generate 통합 호출.

    Returns:
        stream=False: {'text': str, 'prompt_tokens': int, 'completion_tokens': int}
        stream=True:  {'stream': Generator[str, None, None]}
        오류 시:      {'error': str}
    """
    body = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "num_ctx": num_ctx,
        },
    }
    if format:
        body["format"] = format

    try:
        response = requests.post(
            f"{ollama_url}/api/generate",
            json=body,
            timeout=timeout,
            stream=stream,
        )
        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}"}

        if stream:
            def token_generator() -> Generator[str, None, None]:
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        token = chunk.get("response", "")
                        if token:
                            yield token
                    except json.JSONDecodeError:
                        continue

            return {"stream": token_generator()}

        result = response.json()
        return {
            "text": result.get("response", "").strip(),
            "prompt_tokens": result.get("prompt_eval_count", 0),
            "completion_tokens": result.get("eval_count", 0),
        }

    except requests.ConnectionError:
        return {"error": "Ollama 연결 실패"}
    except requests.Timeout:
        return {"error": "Ollama 응답 시간 초과"}
    except Exception as e:
        return {"error": str(e)}


def generate_json(
    prompt: str,
    model: str,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    *,
    temperature: float = 0.3,
    num_predict: int = 4096,
    num_ctx: int = 32768,
    timeout: int = 180,
) -> tuple[Optional[dict | list], str]:
    """JSON 형식 응답 요청. (parsed_json, error_msg) 반환."""
    result = generate(
        prompt, model, ollama_url,
        temperature=temperature,
        num_predict=num_predict,
        num_ctx=num_ctx,
        timeout=timeout,
        format="json",
    )
    if result.get("error"):
        return None, result["error"]

    raw = result.get("text", "")
    if not raw:
        return None, "빈 응답"

    try:
        return json.loads(raw), ""
    except json.JSONDecodeError as e:
        m = re.search(r'[\[{].*[\]}]', raw, re.S)
        if m:
            try:
                return json.loads(m.group()), ""
            except json.JSONDecodeError:
                pass
        return None, f"JSON 파싱 오류: {e}"


def answer_general_question(
    question: str,
    model: str,
    ollama_url: str,
    use_streaming: bool,
) -> dict:
    """문서 근거형 QA 대신 일반 LLM 답변 (문서 외)."""
    from .knowledge_mode import wrap_general_only
    from .prompt_registry import render_prompt

    prompt = render_prompt("general.answer", question=question)
    result = generate(
        prompt, model, ollama_url,
        stream=use_streaming,
        temperature=0.4,
        num_predict=1200,
        num_ctx=16384,
        timeout=120,
    )
    if result.get("error"):
        return {"answer": wrap_general_only(f"일반 답변 생성 실패: {result['error']}")}
    if result.get("stream"):
        # Caller should prefer non-stream for labeled wrap; expose raw stream
        return {"answer_stream": result["stream"], "knowledge_layer": "general_only"}
    text = (result.get("text") or "답변 생성 실패").strip()
    return {"answer": wrap_general_only(text), "knowledge_layer": "general_only"}


def supplement_general_knowledge(
    question: str,
    document_answer: str,
    model: str,
    ollama_url: str,
) -> str:
    """After a document-grounded answer, add a separate general-knowledge supplement."""
    from .prompt_registry import render_prompt

    prompt = render_prompt(
        "general.supplement",
        question=question,
        document_answer=(document_answer or "")[:4000],
    )
    result = generate(
        prompt, model, ollama_url,
        stream=False,
        temperature=0.3,
        num_predict=600,
        num_ctx=16384,
        timeout=90,
    )
    if result.get("error"):
        return ""
    return (result.get("text") or "").strip()
