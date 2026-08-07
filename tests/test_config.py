"""Tests fuer die Aufloesung des Config-Pfads (Projekt- und Benutzerverzeichnis)."""

import json

import pytest

from ki_council.config import (
    CONFIG_ENV_VAR,
    DEFAULT_CONFIG_NAME,
    XDG_CONFIG_HOME_ENV_VAR,
    load_config,
    resolve_config_path,
    user_config_dir,
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Kappt cwd und HOME/XDG, damit kein echter Config-Fund durchschlaegt."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)

    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    monkeypatch.delenv(XDG_CONFIG_HOME_ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workdir)
    return workdir, home


def _write_config(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_env_var_schlaegt_alles(isolated_home, tmp_path, monkeypatch):
    workdir, home = isolated_home
    _write_config(workdir / DEFAULT_CONFIG_NAME, {"quelle": "projekt"})
    _write_config(home / ".config" / "ki-council" / "config.json", {"quelle": "user"})
    explizit = _write_config(tmp_path / "explizit.json", {"quelle": "env"})

    monkeypatch.setenv(CONFIG_ENV_VAR, str(explizit))

    assert resolve_config_path() == explizit
    assert load_config() == {"quelle": "env"}


def test_projektdatei_schlaegt_benutzerverzeichnis(isolated_home):
    workdir, home = isolated_home
    projekt = _write_config(workdir / DEFAULT_CONFIG_NAME, {"quelle": "projekt"})
    _write_config(home / ".config" / "ki-council" / "config.json", {"quelle": "user"})

    assert resolve_config_path() == projekt
    assert load_config() == {"quelle": "projekt"}


def test_benutzerverzeichnis_wird_gefunden(isolated_home):
    _workdir, home = isolated_home
    user_cfg = _write_config(
        home / ".config" / "ki-council" / "config.json", {"quelle": "user"}
    )

    assert resolve_config_path() == user_cfg
    assert load_config() == {"quelle": "user"}


def test_benutzerverzeichnis_akzeptiert_alten_dateinamen(isolated_home):
    _workdir, home = isolated_home
    user_cfg = _write_config(
        home / ".config" / "ki-council" / DEFAULT_CONFIG_NAME, {"quelle": "user-alt"}
    )

    assert resolve_config_path() == user_cfg
    assert load_config() == {"quelle": "user-alt"}


def test_config_json_schlaegt_alten_dateinamen(isolated_home):
    _workdir, home = isolated_home
    verzeichnis = home / ".config" / "ki-council"
    bevorzugt = _write_config(verzeichnis / "config.json", {"quelle": "user"})
    _write_config(verzeichnis / DEFAULT_CONFIG_NAME, {"quelle": "user-alt"})

    assert resolve_config_path() == bevorzugt


def test_xdg_config_home_wird_beachtet(isolated_home, tmp_path, monkeypatch):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv(XDG_CONFIG_HOME_ENV_VAR, str(xdg))
    user_cfg = _write_config(xdg / "ki-council" / "config.json", {"quelle": "xdg"})

    assert user_config_dir() == xdg / "ki-council"
    assert resolve_config_path() == user_cfg


def test_ohne_config_none(isolated_home):
    assert resolve_config_path() is None
    assert load_config() == {}


def _lade_rohtext(home, rohtext):
    pfad = home / ".config" / "ki-council" / "config.json"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(rohtext, encoding="utf-8")
    return load_config()


def test_benutzerconfig_toleriert_kommentare_und_trailing_comma(isolated_home):
    _workdir, home = isolated_home
    roh = '{\n  // Kommentar\n  "openai_model": "gpt-4o-mini",\n}\n'

    assert _lade_rohtext(home, roh) == {"openai_model": "gpt-4o-mini"}


def test_zeilenkommentar_frisst_nicht_den_rest_der_datei(isolated_home):
    """Regression: ".*$" unter re.DOTALL loeschte ab dem ersten "//" alles."""
    _workdir, home = isolated_home
    roh = (
        "{\n"
        '  // erster Kommentar\n'
        '  "openai_model": "gpt-4o-mini",\n'
        "  # zweiter Kommentar\n"
        '  "judge_model": "gpt-4o-mini"\n'
        "}\n"
    )

    assert _lade_rohtext(home, roh) == {
        "openai_model": "gpt-4o-mini",
        "judge_model": "gpt-4o-mini",
    }


def test_blockkommentar_wird_entfernt(isolated_home):
    _workdir, home = isolated_home
    roh = '{\n  /* mehrzeiliger\n     Kommentar */\n  "openai_model": "gpt-4o-mini"\n}\n'

    assert _lade_rohtext(home, roh) == {"openai_model": "gpt-4o-mini"}


def test_doppelslash_in_werten_bleibt_erhalten(isolated_home):
    """Kein False-Positive: "https://" ist kein Kommentar."""
    _workdir, home = isolated_home
    roh = '{\n  "openai_base_url": "https://api.openai.com/v1"\n}\n'

    assert _lade_rohtext(home, roh) == {
        "openai_base_url": "https://api.openai.com/v1"
    }


def test_smart_quotes_werden_toleriert(isolated_home):
    _workdir, home = isolated_home
    roh = '{\n  „openai_model": "gpt-4o-mini"\n}\n'

    assert _lade_rohtext(home, roh) == {"openai_model": "gpt-4o-mini"}
