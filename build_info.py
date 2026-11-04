"""빌드 번호·빌드 시각을 static/build.json 에 기록 (Render buildCommand 에서 실행).

빌드 번호는 저장소의 BUILD 파일 값(커밋할 때마다 1씩 올림)을 쓴다.
"""
import datetime as dt
import json
import os
import subprocess
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))


def collect(built_at=None):
    try:
        number = open(os.path.join(BASE, "BUILD"), encoding="utf-8").read().strip()
    except OSError:
        number = "?"
    commit = os.environ.get("RENDER_GIT_COMMIT", "")
    if not commit:
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BASE, capture_output=True,
                                    text=True, timeout=5).stdout.strip()
        except Exception:
            commit = ""
    built = built_at or dt.datetime.now(ZoneInfo("Asia/Seoul"))
    return {"number": number, "commit": commit[:7], "built_at": built.isoformat(timespec="seconds")}


def load():
    """build.json 이 있으면 읽고, 없으면(로컬 실행 등) 지금 값을 계산."""
    try:
        with open(os.path.join(BASE, "static", "build.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return collect()


if __name__ == "__main__":
    info = collect()
    with open(os.path.join(BASE, "static", "build.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False)
    print("build info:", info)
