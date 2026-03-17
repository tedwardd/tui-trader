# conftest.py — must set env vars at module level, before any app.* import
# app.config runs side-effecting code (sys.exit, EnvironmentError) at import
# time, so these must be set before pytest collects any test module.
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="tui_trader_test_")
_config_home = _tmp + "/config"
_data_home = _tmp + "/data"

os.environ["XDG_CONFIG_HOME"] = _config_home
os.environ["XDG_DATA_HOME"] = _data_home
os.environ["KRAKEN_API_KEY"] = "test_api_key"
os.environ["KRAKEN_API_SECRET"] = "test_api_secret"

# Pre-create the config file so _bootstrap_config() doesn't sys.exit(0)
_config_dir = Path(_config_home) / "tui-trader"
_config_dir.mkdir(parents=True, exist_ok=True)
(_config_dir / "config.env").write_text(
    "KRAKEN_API_KEY=test_api_key\nKRAKEN_API_SECRET=test_api_secret\n"
)

# Pre-create the data dir so _bootstrap_data() doesn't fail
_data_dir = Path(_data_home) / "tui-trader"
_data_dir.mkdir(parents=True, exist_ok=True)

import pytest
from sqlmodel import create_engine, SQLModel
from sqlmodel import Session


@pytest.fixture
def db_engine(monkeypatch):
    """
    Redirect the database to a fresh in-memory SQLite for each test.
    Patches app.database.engine so all CRUD functions use the test DB.
    """
    import app.database as db_module

    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)

    # Run the stop_loss_price migration against the in-memory DB
    import sqlite3
    # In-memory SQLite doesn't need the migration (table is created fresh)
    # but we patch the engine so all db calls go to it
    monkeypatch.setattr(db_module, "engine", engine)
    return engine


@pytest.fixture
def db_session(db_engine):
    """Provide a Session bound to the test engine."""
    with Session(db_engine) as session:
        yield session
