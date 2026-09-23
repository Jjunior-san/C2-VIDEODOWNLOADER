from pathlib import Path

from c2_update import installer_parameters, is_newer, version_key


def test_version_key():
    assert version_key("v1.2.3") == (1, 2, 3)
    assert version_key("2026.06.28.234618") == (2026, 6, 28, 234618)


def test_is_newer():
    assert is_newer("1.1.0", "1.0.9")
    assert not is_newer("1.1.0", "1.1.0")
    assert not is_newer("1.0.9", "1.1.0")


def test_installer_parameters_enable_relaunch_and_diagnostic_log():
    parameters = installer_parameters(Path(r"C:\Temp Folder\c2-update.log"))

    assert "/SILENT" in parameters
    assert "/CLOSEAPPLICATIONS" in parameters
    assert "/C2AUTOUPDATE=1" in parameters
    assert '/LOG="C:\\Temp Folder\\c2-update.log"' in parameters


def test_inno_setup_reopens_app_after_automatic_update():
    script = (Path(__file__).parents[1] / "installer.iss").read_text(encoding="utf-8")

    assert "function IsAutomaticUpdate: Boolean;" in script
    assert "WizardSilent or" in script
    assert 'Parameters: "--updated"' in script
    assert "Check: IsAutomaticUpdate" in script
