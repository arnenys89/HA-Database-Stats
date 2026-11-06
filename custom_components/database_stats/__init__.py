"""Database Statistics integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from sqlalchemy import MetaData, Table, cast, func, inspect, select, text
from sqlalchemy.types import LargeBinary

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.components.recorder import get_instance

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]


def _top_entity_queries(connection, engine, tables: set[str]) -> dict:
    """Find the busiest and largest entities using read-only aggregate queries."""
    result = {
        "top_event_entity": None,
        "top_event_count": 0,
        "top_state_entity": None,
        "top_state_count": 0,
        "top_memory_entity": None,
        "top_memory_bytes": 0,
        "top_memory_states": 0,
    }
    if not {"states", "states_meta"}.issubset(tables):
        return result

    metadata = MetaData()
    states = Table("states", metadata, autoload_with=connection)
    states_meta = Table("states_meta", metadata, autoload_with=connection)

    state_counts = (
        select(
            states_meta.c.entity_id.label("entity_id"),
            func.count().label("entry_count"),
        )
        .select_from(states.join(states_meta, states.c.metadata_id == states_meta.c.metadata_id))
        .group_by(states_meta.c.entity_id)
        .order_by(func.count().desc())
        .limit(1)
    )
    row = connection.execute(state_counts).first()
    if row:
        result["top_state_entity"] = row.entity_id
        result["top_state_count"] = int(row.entry_count)

    # Estimate payload size from state text plus the referenced shared attributes.
    if "state_attributes" in tables and "attributes_id" in states.c:
        attrs = Table("state_attributes", metadata, autoload_with=connection)
        if "shared_attrs" in attrs.c:
            if engine.dialect.name == "sqlite":
                state_bytes = func.length(cast(states.c.state, LargeBinary))
                attrs_bytes = func.length(cast(attrs.c.shared_attrs, LargeBinary))
            elif engine.dialect.name in ("mysql", "mariadb"):
                state_bytes = func.length(states.c.state)
                attrs_bytes = func.length(attrs.c.shared_attrs)
            else:
                state_bytes = func.octet_length(states.c.state)
                attrs_bytes = func.octet_length(attrs.c.shared_attrs)

            payload_size = func.coalesce(func.sum(state_bytes + func.coalesce(attrs_bytes, 0)), 0)
            memory_query = (
                select(
                    states_meta.c.entity_id.label("entity_id"),
                    payload_size.label("payload_bytes"),
                    func.count().label("entry_count"),
                )
                .select_from(
                    states.join(states_meta, states.c.metadata_id == states_meta.c.metadata_id)
                    .outerjoin(attrs, states.c.attributes_id == attrs.c.attributes_id)
                )
                .group_by(states_meta.c.entity_id)
                .order_by(payload_size.desc())
                .limit(1)
            )
            row = connection.execute(memory_query).first()
            if row:
                result["top_memory_entity"] = row.entity_id
                result["top_memory_bytes"] = int(row.payload_bytes or 0)
                result["top_memory_states"] = int(row.entry_count or 0)

    # Count events that explicitly refer to an entity in state_changed event data.
    if {"events", "event_types"}.issubset(tables):
        events = Table("events", metadata, autoload_with=connection)
        event_types = Table("event_types", metadata, autoload_with=connection)
        if {"event_type_id", "event_data"}.issubset(events.c) and "event_type" in event_types.c:
            if engine.dialect.name == "sqlite":
                entity_expr = func.json_extract(events.c.event_data, "$.entity_id")
            elif engine.dialect.name in ("mysql", "mariadb"):
                entity_expr = func.json_unquote(func.json_extract(events.c.event_data, "$.entity_id"))
            elif engine.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import JSONB
                entity_expr = cast(events.c.event_data, JSONB).op("->>")("entity_id")
            else:
                entity_expr = None

            if entity_expr is not None:
                event_query = (
                    select(
                        entity_expr.label("entity_id"),
                        func.count().label("entry_count"),
                    )
                    .select_from(events.join(
                        event_types,
                        events.c.event_type_id == event_types.c.event_type_id,
                    ))
                    .where(event_types.c.event_type == "state_changed")
                    .where(entity_expr.is_not(None))
                    .group_by(entity_expr)
                    .order_by(func.count().desc())
                    .limit(1)
                )
                row = connection.execute(event_query).first()
                if row:
                    result["top_event_entity"] = row.entity_id
                    result["top_event_count"] = int(row.entry_count)

    return result


def _read_database_stats(engine) -> dict:
    """Collect aggregate database statistics using read-only queries."""
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    tables = set(table_names)
    result: dict = {
        "database_size_bytes": None,
        "states": None,
        "events": None,
        "entities": None,
        "statistics": None,
        "last_state_update": None,
        "database_engine": engine.dialect.name,
    }

    table_map = {
        "states": "states",
        "events": "events",
        "entities": "states_meta",
        "statistics": "statistics",
    }
    with engine.connect() as connection:
        for key, table_name in table_map.items():
            if table_name in tables:
                reflected = Table(table_name, MetaData(), autoload_with=connection)
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
                states_table = Table("states", MetaData(), autoload_with=connection)
                result["last_state_update"] = connection.execute(
                    select(func.max(states_table.c[timestamp_column]))
                ).scalar_one_or_none()

        result.update(_top_entity_queries(connection, engine, tables))

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
