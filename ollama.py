"""Ollama compatibility shim.

The CNC app no longer installs or requires the external Ollama package.
This module exists only so older app code can start without ModuleNotFoundError.
AI receipt analysis is intentionally disabled.
"""


def chat(*args, **kwargs):
    raise RuntimeError("Ollama AI 기능이 비활성화되어 있습니다. 영수증 AI 자동분석을 사용할 수 없습니다.")
