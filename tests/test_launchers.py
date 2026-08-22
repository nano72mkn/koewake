"""ランチャースクリプトの決まりごとを固定する。"""

from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _bat_files() -> list[Path]:
    return sorted(SCRIPTS.glob("*.bat"))


def test_bat_files_exist():
    assert _bat_files(), "scripts/ に .bat が無い"


@pytest.mark.parametrize("path", _bat_files(), ids=lambda p: p.name)
def test_bat_files_are_ascii_only(path: Path):
    """`.bat` に日本語を書かない。

    cmd.exe は日本語版Windowsで .bat を CP932 として読むため、UTF-8 で
    保存した日本語が文字化けし、その断片をコマンドとして実行してしまう
    （'○○' は、内部コマンドまたは外部コマンドとして認識されていません）。
    画面に出す文言は Python 側（koewake/prompt.py）に置くこと。
    """
    raw = path.read_bytes()
    non_ascii = [(i, b) for i, b in enumerate(raw) if b > 0x7F]
    assert not non_ascii, (
        f"{path.name} に非ASCII文字がある（先頭の位置 {non_ascii[0][0]}）。"
        "日本語は Python 側に置くこと。"
    )


@pytest.mark.parametrize("path", _bat_files(), ids=lambda p: p.name)
def test_bat_files_keep_utf8_console(path: Path):
    """Python 側が日本語を出すので、コンソールは UTF-8 にしておく。"""
    text = path.read_text(encoding="ascii")
    assert "chcp 65001" in text
    assert "PYTHONUTF8=1" in text


def test_launcher_delegates_the_prompt_to_python():
    for name in ("koewake.bat", "koewake.command"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "--ask-speakers" in text, f"{name} が対話を Python に任せていない"


def test_setup_scripts_show_guidance_from_python():
    for name in ("setup-windows.bat", "setup-macos.command"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "--welcome" in text, f"{name} が案内を Python に任せていない"
