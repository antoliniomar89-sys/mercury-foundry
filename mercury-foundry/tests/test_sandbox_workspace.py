from mercury_foundry.sandbox.workspace import SandboxViolation, Workspace


def test_write_inside_sandbox_succeeds(tmp_path):
    ws = Workspace(tmp_path / "sandbox")
    record = ws.write_file("module.py", "x = 1\n")
    assert record.created is True
    assert (tmp_path / "sandbox" / "module.py").read_text() == "x = 1\n"


def test_overwrite_produces_diff(tmp_path):
    ws = Workspace(tmp_path / "sandbox")
    ws.write_file("module.py", "x = 1\n")
    record = ws.write_file("module.py", "x = 2\n")
    assert record.created is False
    assert "-x = 1" in record.diff
    assert "+x = 2" in record.diff


def test_path_traversal_outside_sandbox_is_blocked(tmp_path):
    ws = Workspace(tmp_path / "sandbox")
    try:
        ws.write_file("../outside.py", "danger = True\n")
        assert False, "doveva sollevare SandboxViolation"
    except SandboxViolation:
        pass
    assert not (tmp_path / "outside.py").exists()


def test_absolute_path_is_blocked(tmp_path):
    ws = Workspace(tmp_path / "sandbox")
    try:
        ws.write_file("/etc/passwd", "danger\n")
        assert False, "doveva sollevare SandboxViolation"
    except SandboxViolation:
        pass
