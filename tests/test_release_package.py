from tools import release_package


def _mock_preflight_dependencies(monkeypatch, tmp_path):
    project = tmp_path / "PaperMuse.xcodeproj"
    project.mkdir()
    monkeypatch.setattr(release_package, "APP_PROJECT", project)
    monkeypatch.setattr(release_package.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(release_package, "_identity_errors", lambda _identity: [])
    monkeypatch.setattr(release_package, "_notary_errors", lambda _profile: [])


def test_release_preflight_requires_main_runtime_env(monkeypatch, tmp_path):
    _mock_preflight_dependencies(monkeypatch, tmp_path)

    errors = release_package.preflight("Developer ID Application: Example", "profile", {})

    assert any("PAPER_MUSE_MAIN_RUNTIME_URL" in e for e in errors)
    assert any("PAPER_MUSE_MAIN_RUNTIME_SHA256" in e for e in errors)


def test_release_preflight_accepts_required_inputs(monkeypatch, tmp_path):
    _mock_preflight_dependencies(monkeypatch, tmp_path)
    env = {
        "PAPER_MUSE_MAIN_RUNTIME_URL": "https://downloads.example.test/papermuse-runtime.tar.gz",
        "PAPER_MUSE_MAIN_RUNTIME_SHA256": "a" * 64,
    }

    assert release_package.preflight("Developer ID Application: Example", "profile", env) == []


def test_release_preflight_accepts_embedded_runtime_file(monkeypatch, tmp_path):
    _mock_preflight_dependencies(monkeypatch, tmp_path)
    runtime = tmp_path / "main-runtime.tar.gz"
    runtime.write_bytes(b"runtime")

    assert release_package.preflight(
        "Developer ID Application: Example",
        "profile",
        {"PAPER_MUSE_MAIN_RUNTIME_FILE": str(runtime)},
    ) == []
