"""Database Statistics integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from sqlalchemy import func, inspect, select

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.components.recorder import get_instance

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]


def _read_database_stats(engine) -> dict:
    """Collect aggregate database statistics using read-only queries."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    result: dict = {
        "database_size_bytes": None,
        "states": None,
        "events": None,
        "entities": None,
        "statistics": None,
        "last_state_update": None,
        "database_engine": engine.dialect.name,
    }

    # Count only known Recorder tables; identifiers come from this fixed allow-list.
    table_map = {
        "states": "states",
        "events": "events",
        "entities": "states_meta",
        "statistics": "statistics",
    }
    with engine.connect() as connection:
        for key, table_name in table_map.items():
            if table_name in tables:
                table = next(t for t in inspector.get_table_names() if t == table_name)
                # Table names above are fixed Recorder schema identifiers.
                from sqlalchemy import Table, MetaData
                reflected = Table(table, MetaData(), autoload_with=connection)
                result[key] = connection.execute(
                    select(func.count()).select_from(reflected)
                ).scalar_one()

        if "states" in tables:
            columns = {column["name"] for column in inspector.get_columns("states")}
            timestamp_column = (
                "last_updated_ts" if "last_updated_ts" in columns
                else "last_updated" if "last_updated" in columns
                else None
            )
            if timestamp_column:
                from sqlalchemy import Table, MetaData
                states_table = Table("states", MetaData(), autoload_with=connection)
                result["last_state_update"] = connection.execute(
                    select(func.max(states_table.c[timestamp_column]))
                ).scalar_one_or_none()

    # SQLite database file size. Server-side database storage is not exposed as a file.
    if engine.dialect.name == "sqlite" and engine.url.database:
        try:
            from pathlib import Path
            result["database_size_bytes"] = Path(engine.url.database).stat().st_size
        except (OSError, TypeError):
            _LOGGER.debug("Could not determine SQLite database file size")

    return result


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration from configuration.yaml (not used)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Database Statistics."""
    recorder = get_instance(hass)
    interval = timedelta(minutes=entry.options.get("scan_interval", 5))

    async def async_update_data() -> dict:
        try:
            if recorder.engine is None:
                raise UpdateFailed("Recorder database engine is not ready")
            return await recorder.async_add_executor_job(
                _read_database_stats, recorder.engine
            )
        except Exception as err:
            raise UpdateFailed(f"Could not read Recorder database: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="Database Statistics",
        update_interval=interval,
        update_method=async_update_data,
    )
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload integration."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
