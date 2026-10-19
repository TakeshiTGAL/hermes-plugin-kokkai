"""Tool Search の既定で、この本の skill が system prompt の一覧に出る。

モデルは呼ばない。ネットワークへは出ない。v0.22.2 の Python が無いときは skip する。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _v0222_python() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        python = parent / ("." + "harness") / "venv" / "v0222" / "bin" / "python"
        if python.is_file():
            return python
    return None


def _skill_names(repo: Path) -> list[str]:
    plugin_name = ""
    for line in (repo / "plugin.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("name:"):
            plugin_name = line.split(":", 1)[1].strip().strip("\"'")
            break
    names = []
    for path in sorted((repo / "skills").glob("*/SKILL.md")):
        skill = path.parent.name
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("name:"):
                skill = line.split(":", 1)[1].strip().strip("\"'")
                break
        names.append(f"{plugin_name}:{skill}")
    return names


def _child() -> int:
    repo = Path(sys.argv[1]).resolve()
    expected = _skill_names(repo)
    if not expected:
        print("SKILL_INDEX_NONE")
        return 1
    scratch = Path(tempfile.mkdtemp(prefix="skill-index-"))
    home = scratch / "home"
    home.mkdir()
    tree = scratch / "tree"
    shutil.copytree(
        repo,
        tree,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", "node_modules", ".venv", "*.pyc"),
    )
    git_env = dict(os.environ)
    git_env.update({
        "GIT_AUTHOR_NAME": "skill-index",
        "GIT_AUTHOR_EMAIL": "skill-index@example.com",
        "GIT_COMMITTER_NAME": "skill-index",
        "GIT_COMMITTER_EMAIL": "skill-index@example.com",
    })
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True, env=git_env)
    subprocess.run(["git", "add", "-A"], cwd=tree, check=True, env=git_env)
    subprocess.run(
        ["git", "-c", "user.name=skill-index", "-c", "user.email=skill-index@example.com",
         "commit", "-q", "-m", "skill index tree"],
        cwd=tree, check=True, env=git_env,
    )
    env = dict(git_env)
    env.update({
        "HERMES_HOME": str(home),
        "HERMES_DISABLE_UPDATE_CHECK": "1",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "",
    })
    # hermes plugins install は、有効化のときに単独の Python を
    # hermes-assets から取る。ここは file:// の git を一時の家へ clone し、
    # config で有効にする。tool_search は書かない（既定の auto）。
    plugin_name = expected[0].split(":", 1)[0]
    dest = home / "plugins" / plugin_name
    dest.parent.mkdir(parents=True)
    cloned = subprocess.run(
        ["git", "clone", "-q", tree.as_uri(), str(dest)],
        cwd="/tmp", env=env, capture_output=True, text=True,
    )
    if cloned.returncode != 0:
        print(cloned.stdout)
        print(cloned.stderr)
        print("SKILL_INDEX_INSTALL_FAIL")
        return cloned.returncode or 1
    (home / "config.yaml").write_text(
        f"plugins:\n  enabled:\n    - {plugin_name}\n",
        encoding="utf-8",
    )
    os.environ.update(env)
    os.chdir("/tmp")
    from run_agent import AIAgent
    from agent.system_prompt import build_system_prompt

    agent = AIAgent(
        base_url="http://127.0.0.1:9/v1", api_key="sk-fake-not-real", provider="custom",
        model="fake-model", quiet_mode=True, skip_memory=True, platform="cli",
        skip_context_files=True,
    )
    prompt = build_system_prompt(agent)
    start = prompt.find("<available_skills>")
    end = prompt.find("</available_skills>")
    block = prompt[start:end] if start != -1 and end != -1 else ""
    missing = [name for name in expected if name not in block]
    print(block)
    if missing:
        print("SKILL_INDEX_MISS", " ".join(missing))
        return 1
    print("SKILL_INDEX_HIT", " ".join(expected))
    return 0


def test_skill_is_listed_under_default_tool_search():
    import pytest

    python = _v0222_python()
    if python is None:
        pytest.skip("v0.22.2 の Python が無いので、system prompt の skill 一覧は組み立てていない")
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [str(python), str(Path(__file__).resolve()), str(repo), str(python)],
        capture_output=True, text=True, timeout=180,
    )
    tail = "\n".join((proc.stdout or "").splitlines()[-8:])
    assert proc.returncode == 0, tail + "\n" + (proc.stderr or "")[-800:]
    assert "SKILL_INDEX_HIT" in proc.stdout


if __name__ == "__main__":
    raise SystemExit(_child())
