"""Preflight checks cannot establish live or experimental readiness."""

from pathlib import Path

import pytest

from demo.readiness import check_readiness


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/app.js").write_text("export {};", encoding="utf-8")
    (tmp_path / "assets/app.css").write_text("body {}", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<script type="module" src="./assets/app.js"></script>'
        '<link rel="stylesheet" href="./assets/app.css">', encoding="utf-8",
    )
    return tmp_path


def test_preflight_matches_built_files_and_preserves_scope(dist):
    report = check_readiness(dist)
    assert report["local_presentation_ready"]
    assert all(report["checks"].values())
    assert "not HTTP, hosted deployment, live agent" in report["scope"]


@pytest.mark.parametrize("asset", [
    "./assets/missing.js", "../outside.js", "https://example.com/app.js",
    "./assets/../../outside.js",
])
def test_preflight_rejects_missing_external_or_escaping_assets(dist, asset):
    (dist / "index.html").write_text(f'<script src="{asset}"></script>', encoding="utf-8")
    assert check_readiness(dist)["checks"]["frontend_assets"] is False


def test_missing_build_is_actionable(tmp_path):
    report = check_readiness(tmp_path)
    assert report["local_presentation_ready"] is False
    assert "npm run build" in report["errors"][0]


def test_fixture_failure_does_not_invent_readiness(dist, monkeypatch):
    def missing():
        raise ValueError("missing")

    monkeypatch.setattr("demo.readiness.rehearsal_catalog", missing)
    report = check_readiness(dist)
    assert report["local_presentation_ready"] is False
    assert report["checks"]["rehearsal_fixtures"] is False


def test_launcher_check_starts_no_server(dist, monkeypatch, capsys):
    from demo import launcher

    monkeypatch.setattr("sys.argv", ["demo.launcher", "--check"])
    monkeypatch.setattr(launcher, "check_readiness", lambda: check_readiness(dist))
    monkeypatch.setattr(launcher, "launch_demo", lambda **kwargs: pytest.fail("server started"))
    launcher.main()
    assert '"local_presentation_ready": true' in capsys.readouterr().out


def test_launcher_rejects_incomplete_build_before_server_start(monkeypatch):
    from demo import launcher

    monkeypatch.setattr(launcher, "check_readiness", lambda: {
        "local_presentation_ready": False, "errors": ["Missing build"],
    })
    with pytest.raises(RuntimeError, match="Missing build"):
        launcher.launch_demo()
