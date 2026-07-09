import tarfile

from tools import build_main_runtime


def test_runtime_builder_removes_bytecode(tmp_path):
    root = tmp_path / "runtime"
    cache = root / "pkg" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "mod.cpython-312.pyc").write_bytes(b"bytecode")
    (root / "pkg" / "mod.pyo").write_bytes(b"optimized")
    (root / "pkg" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")

    build_main_runtime._remove_bytecode(root)

    assert not cache.exists()
    assert not (root / "pkg" / "mod.pyo").exists()
    assert (root / "pkg" / "mod.py").exists()


def test_runtime_builder_scrubs_text_prefixes_only(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    text = root / "config.py"
    binary = root / "extension.so"
    text.write_text("prefix = '/Users/example/.venv'\n", encoding="utf-8")
    binary.write_bytes(b"\0/Users/example/.venv")

    build_main_runtime._scrub_text_prefixes(root, ["/Users/example/.venv"])

    assert "/Users/example" not in text.read_text(encoding="utf-8")
    assert build_main_runtime.TEXT_SCRUB_PLACEHOLDER in text.read_text(encoding="utf-8")
    assert binary.read_bytes() == b"\0/Users/example/.venv"


def test_runtime_builder_tar_filter_removes_local_owner_metadata():
    info = tarfile.TarInfo("main/bin/python")
    info.uid = 501
    info.gid = 20
    info.uname = "local-user"
    info.gname = "staff"
    info.mtime = 123

    filtered = build_main_runtime._tar_filter(info)

    assert filtered.uid == 0
    assert filtered.gid == 0
    assert filtered.uname == ""
    assert filtered.gname == ""
    assert filtered.mtime == 0


def test_runtime_builder_detects_macho_by_magic(tmp_path):
    macho = tmp_path / "extension.so"
    text = tmp_path / "module.py"
    macho.write_bytes(b"\xcf\xfa\xed\xfepayload")
    text.write_text("print('not macho')\n", encoding="utf-8")

    assert build_main_runtime._is_macho(macho)
    assert not build_main_runtime._is_macho(text)


def test_runtime_builder_replaces_build_paths_without_changing_size(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    binary = root / "extension.so"
    original = b"prefix:/private/var/folders/example:suffix"
    binary.write_bytes(original)

    build_main_runtime._replace_build_path_bytes(root)

    sanitized = binary.read_bytes()
    assert len(sanitized) == len(original)
    assert b"/private/var/folders" not in sanitized
    assert b"/tmp/papermuse-build" in sanitized
