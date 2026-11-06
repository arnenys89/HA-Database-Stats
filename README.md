# Home Assistant Database Statistics

Custom Home Assistant integration that reports read-only statistics about the Recorder database.

## Features
- Database file size (SQLite; 0 for database servers where file size is not available)
- Number of recorded states, events, entity metadata rows and long-term statistics
- Last recorded state update
- Entity with the most `state_changed` event entries
- Entity with the most state rows
- Entity with the largest estimated stored payload (state text plus shared attributes)
- Automatic refresh, configurable from 1 to 60 minutes
- No direct database writes and no external dependencies

## Installation with HACS
1. In HACS, add this repository as a **Custom repository** (Integration).
2. Install **Database Statistics**.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and add **Database Statistics**.

## Notes
The integration reads the database configured for Home Assistant's built-in Recorder. It uses Recorder's existing SQLAlchemy engine and executes read-only aggregate queries in Recorder's database executor. SQLite file size is reported for the local database file. For MariaDB/MySQL/PostgreSQL the file size is not meaningful or accessible from Home Assistant, so the sensor is unavailable.

The top-event ranking counts `state_changed` events that contain an `entity_id`. The payload ranking estimates the sum of state text and referenced shared attributes per entity; it is not an exact measurement of physical database pages or index overhead. These rankings are exposed as diagnostic sensors with their counts/size in attributes.

This integration is independent of the Recorder configuration. It does not purge, migrate, optimize, or modify the database.
