"""\
Plugin for the "Deye Solar Inverter MQTT Bridge" which enables automatic
discovery of all published values in Home Assistant.
"""

# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

# Copyright (c) 2024-2026 Carsten Grohmann

import fnmatch
import functools
import json
import logging
import re
from typing import Any

from deye_config import DeyeConfig, DeyeEnv
from deye_events import DeyeEventProcessor, DeyeEventList, DeyeObservationEvent
from deye_mqtt import DeyeMqttClient
from deye_observation import Observation
from deye_plugin_loader import DeyePluginContext


RELEASE_DATE = "2026-04-08"


class DeyeHADiscovery(DeyeEventProcessor):
    """Plugin for HA discovery topics"""

    _active_power_regulation_enabled: bool
    """Publish control for active power regulation"""

    _multi_inverter_logger_count: int
    """Number of inverters configured in multi-inverter mode"""

    _multi_inverter_data_aggregator_enabled: bool
    """Data aggregation enabled in multi-inverter mode"""

    _device_name: str
    """Device name shown in HA"""

    _ignore_topic_patterns: tuple
    """List of user-specific topics to be ignored"""

    _ignore_default_topic_patterns: tuple[str] = (
        "settings/active_power_regulation",
        "ac/relay_status",
    )
    """List of topics that are always ignored"""

    _logger_descriptions: dict[int, str]
    """Logger descriptions, keyed by 1-based logger index"""

    _logger_serial: int
    """Logger (inverter) serial number"""

    _use_topic_in_unique_id: bool
    """Use MQTT topic instead of sensor name in unique_id"""

    inverter_manufacturer: str
    """Inverter manufacturer"""

    inverter_model: str
    """Inverter model"""

    component_prefix = "deye_inverter_mqtt"
    """\
    Prefix for the component name.

    Use underscore to separate parts to avoid problems with the MQTT topic.
    """

    _sw_version: str
    """Software version string for HA device map"""

    ha_discovery_prefix: str | None
    """MQTT prefix used by homeassistant"""

    expire_after: int | None
    """Expire_after parameter for HA sensors"""

    _config: DeyeConfig
    """Core configuration"""

    _log: logging.Logger
    """Logger for this plugin"""

    _logger_index: int
    """1-based index of the logger (inverter) currently being processed"""

    _mqtt_client: DeyeMqttClient
    """MQTT client for publishing discovery messages"""

    def __init__(self, plugin_context: DeyePluginContext):
        self.expire_after = None
        self.ha_discovery_prefix = None
        self.inverter_model = ""
        self.inverter_manufacturer = ""
        self._active_power_regulation_enabled = DeyeEnv.boolean(
            "DEYE_FEATURE_ACTIVE_POWER_REGULATION", False
        )
        self._config = plugin_context.config
        self._device_name = ""
        self._log = logging.getLogger(DeyeHADiscovery.__name__)
        self._logger_descriptions = {}
        self._logger_index = 0
        self._logger_serial = 0
        self._ignore_topic_patterns = ()
        self._mqtt_client = plugin_context.mqtt_client
        self._multi_inverter_logger_count = 0
        self._multi_inverter_data_aggregator_enabled = False
        self._sw_version = f"deye-inverter-mqtt with {self.get_id()}"
        self._use_topic_in_unique_id = False

    def initialize(self):
        super().initialize()
        self.ha_discovery_prefix = DeyeEnv.string(
            "DEYE_HA_PLUGIN_HA_MQTT_PREFIX", "homeassistant"
        )
        self.inverter_manufacturer = DeyeEnv.string(
            "DEYE_HA_PLUGIN_INVERTER_MANUFACTURER", "Unknown manufacturer"
        )
        self.inverter_manufacturer = self.inverter_manufacturer.strip('"')
        self.inverter_model = DeyeEnv.string(
            "DEYE_HA_PLUGIN_INVERTER_MODEL", "Unknown model"
        )
        self.inverter_model = self.inverter_model.strip('"')
        value = DeyeEnv.string("DEYE_HA_PLUGIN_IGNORE_TOPIC_PATTERNS", "")
        if value:
            self._ignore_topic_patterns = (
                tuple(value.split(":")) + self._ignore_default_topic_patterns
            )
        else:
            self._ignore_topic_patterns = tuple(self._ignore_default_topic_patterns)
        value = DeyeEnv.string("DEYE_HA_PLUGIN_EXPIRE_AFTER", "")
        if value:
            self.expire_after = int(value)
        self._use_topic_in_unique_id = DeyeEnv.boolean(
            "DEYE_HA_PLUGIN_USE_TOPIC_IN_UNIQUE_ID", False
        )
        if self._use_topic_in_unique_id:
            self._log.debug(
                "Feature enabled: Use MQTT topic instead of sensor name in unique_id"
            )
        self._multi_inverter_logger_count = DeyeEnv.integer("DEYE_LOGGER_COUNT", 0)
        if self._multi_inverter_logger_count:
            self._log.debug("Feature enabled: Multi-inverter setup")
            self._multi_inverter_data_aggregator_enabled = DeyeEnv.boolean(
                "DEYE_FEATURE_MULTI_INVERTER_DATA_AGGREGATOR", False
            )
            if self._multi_inverter_data_aggregator_enabled:
                self._log.debug("Feature enabled: Data aggregation")
            for i in range(1, self._multi_inverter_logger_count + 1):
                try:
                    self._logger_descriptions[i] = DeyeEnv.string(
                        f"DEYE_LOGGER_{i}_DESC"
                    )
                except KeyError:
                    sn = self._config.logger_configs[i - 1].serial_number
                    self._logger_descriptions[i] = f"SN {sn}"

    @staticmethod
    def get_id() -> str:
        """Return the plugin identification"""
        return f"HA Discovery Plugin version {RELEASE_DATE}"

    @staticmethod
    @functools.cache
    def _topic_to_object_id(topic: str) -> str:
        """Format the topic to include in another topic string"""
        res = topic.lower()
        res = res.replace("/", "_")
        res = res.strip()
        return res

    def _get_unique_id(self, sensor_name: str, topic_name: str) -> str:
        """Return a unique id for the current sensor"""
        assert sensor_name or topic_name
        if self._use_topic_in_unique_id:
            prefix = self.component_prefix
        else:
            # Do not change the prefix, as a changed unique ID generates new sensors.
            # The prefix differs from self.component_prefix = "deye_inverter_mqtt"
            prefix = "deye_mqtt_inverter"
        if (self._use_topic_in_unique_id and topic_name) or (
            not sensor_name and topic_name
        ):
            component = topic_name
        else:
            component = sensor_name
        _unique_id = f"{prefix}_{self._logger_serial}_{component}".lower()
        _unique_id = _unique_id.replace(" ", "_")
        _unique_id = _unique_id.replace("/", "_")
        return _unique_id

    @staticmethod
    @functools.cache
    def _get_device_class(topic: str) -> tuple[str | None, str]:
        """Return device_class and platform based on a given topic

        Args:
            topic (str): MQTT topic for the sensor value
        """
        device_class = None
        platform = "sensor"

        # topic: ac/l*/voltage
        # topic: dc/pv*/voltage
        # topic: bms/*/charging_voltage
        # topic: bms/*/discharge_voltage
        if (
            topic.endswith("/voltage")
            or topic.endswith("/charging_voltage")
            or topic.endswith("/discharge_voltage")
        ):
            device_class = "voltage"

        # topic: ac/l*/current
        # topic: dc/pv*/current
        # topic: bms/*/charge_current_limit
        # topic: bms/*/discharge_current_limit
        # topic: bms/*/discharge_max_current
        # topic: settings/battery/maximum_*_charge_current
        # topic: settings/battery/maximum_discharge_current
        elif (
            topic.endswith("/current")
            or topic.endswith("/charge_current_limit")
            or topic.endswith("/charging_max_current")
            or topic.endswith("/discharge_max_current")
            or topic.endswith("_charge_current")
            or topic.endswith("_discharge_current")
        ):
            device_class = "current"

        # topic: ac/active_power
        # topic: ac/l*/power
        # topic: ac/total_grid_power
        # topic: ac/total_internal_power
        # topic: ac/ups/power
        # topic: dc/pv*/power
        # topic: dc/total_power
        # topic: operating_power
        # topic: settings/solar_sell_max_power
        elif topic.endswith("power"):
            device_class = "power"

        # topic: battery/(daily|total)_(charge|discharge)
        # topic: (day|total)_energy
        # topic: dc/pv*/(day|total)_energy
        # topic: ac/(total_energy_bought|daily_energy_sold)
        elif (
            topic.endswith("_charge")
            or topic.endswith("_discharge")
            or topic.endswith("_energy")
            or "_energy_" in topic
        ):
            device_class = "energy"

        # topic: ac/l*/ct/(internal|external)
        elif re.match(r"ac/l\d+/ct/(internal|external)", topic):
            device_class = "power"

        # topic: ac/freq
        # topic: ac/frequency
        # topic: ac/grid_frequency
        elif topic.endswith("/freq") or topic.endswith("frequency"):
            device_class = "frequency"

        # topic: ac/temperature
        # topic: battery/temperature
        # topic: battery/*/temperature
        # topic: bms/*/temp
        # topic: radiator_temp
        elif (
            topic.endswith("temperature")
            or topic.endswith("/temp")
            or topic == "radiator_temp"
        ):
            device_class = "temperature"

        # topic: battery/soc
        # topic: bms/*/soc
        elif topic.endswith("/soc"):
            device_class = "battery"

        # topic: active_power_regulation
        elif topic == "active_power_regulation":
            device_class = None
            platform = "number"

        # topic: settings/system_time
        elif topic == "settings/system_time":
            device_class = "timestamp"

        elif topic == "uptime":
            device_class = "duration"

        elif topic in ("inverter/status", "settings/workmode"):
            device_class = "enum"

        # topic: settings/battery/grid_charge
        # topic: settings/solar_sell
        elif topic in ("settings/battery/grid_charge", "settings/solar_sell"):
            platform = "binary_sensor"

        elif topic == "ac/ongrid":
            device_class = "power"
            platform = "binary_sensor"

        elif topic == "application_status":
            device_class = "running"
            platform = "binary_sensor"

        # topic: logger_status or logger_status_{N}
        elif topic.startswith("logger_status"):
            device_class = "connectivity"
            platform = "binary_sensor"

        return device_class, platform

    @staticmethod
    @functools.cache
    def _ignore_topic(topic: str, ignore_list: tuple) -> bool:
        """Check whether the topic matches a pattern in the ignore list

        Args:
            topic (str): MQTT topic for the sensor value
            ignore_list (tuple): Tuple of strings with pattern to ignore the given topic
        """
        res = any(fnmatch.fnmatch(topic, pattern) for pattern in ignore_list)
        return res

    def _get_logger_desc(self, idx: int) -> str:
        """Return a description for the logger (1-based idx) for multi-inverter setups."""
        return self._logger_descriptions.get(idx, f"SN {self._logger_serial}")

    @staticmethod
    @functools.cache
    def _get_state_class(topic: str) -> str:
        """Return state_class based on a given topic

        Args:
            topic (str): MQTT topic for the sensor value
        """
        # topic: ac/(daily|total)_energy_(bought|sold)
        # topic: battery/(daily|total)_(charge|discharge)
        # topic: (day|total)_energy
        # topic: dc/pv*/(day|total)_energy
        # topic: settings/system_time
        # topic: uptime
        if (
            topic.endswith("_charge")
            or topic.endswith("_discharge")
            or topic.endswith("_energy")
            or topic.endswith("_energy_bought")
            or topic.endswith("_energy_sold")
            or topic in ["settings/system_time", "uptime"]
        ):
            state_class = "total_increasing"

        # topic: ac/active_power
        # topic: ac/freq
        # topic: ac/l*/ct/(internal|external)
        # topic: ac/l*/(current|power|voltage)
        # topic: ac/temperature
        # topic: battery/soc
        # topic: battery/temperature
        # topic: dc/pv*/(current|power|voltage)
        # topic: dc/total_power
        # topic: operating_power
        # topic: radiator_temp
        else:
            state_class = "measurement"

        return state_class

    @staticmethod
    @functools.cache
    def _get_options(topic: str) -> list[str]:
        """Return entity options based on a given topic

        Args:
            topic (str): MQTT topic for the sensor value
        """
        options = []
        if topic == "inverter/status":
            options = ["standby", "selfcheck", "normal", "alarm", "fault"]
        elif topic == "settings/workmode":
            options = ["selling_first", "zero_export_to_load", "zero_export_to_ct"]

        return options

    @staticmethod
    @functools.cache
    def _get_payload_on_off(topic: str) -> tuple:
        """Return payload_on and payload_off values for a binary sensor with a given topic

        Args:
            topic (str): MQTT topic for the sensor value
        """
        # SingleRegisterSensor with print_format="{:.0f}" publishes "1"/"0"
        if topic in ("settings/battery/grid_charge", "settings/solar_sell"):
            return "1", "0"
        return "True", "False"

    def _get_discovery_device_map_inverter(
        self, identifier: str
    ) -> dict[str, list[str] | str]:
        """Return the device map for a physical inverter sensor.

        Identifies the sensor as part of the inverter hardware device,
        including manufacturer and serial number.
        """
        return {
            "identifiers": [identifier],
            "manufacturer": self.inverter_manufacturer,
            "model": self.inverter_model,
            "name": self._device_name,
            "serial_number": str(self._logger_serial),
            "sw_version": self._sw_version,
        }

    def _get_discovery_device_map_bridge(
        self, identifier: str
    ) -> dict[str, list[str] | str]:
        """Return the device map for bridge and logger status sensors.

        Status sensors represent the software bridge, not the inverter
        hardware — so serial number is omitted.
        """
        return {
            "identifiers": [identifier],
            "manufacturer": self.inverter_manufacturer,
            "model": "Status MQTT Bridge",
            "name": self._device_name,
            "sw_version": self._sw_version,
        }

    def _fmt_sensor_name(self, sensor_name: str) -> str:
        """Format the sensor name to include in the HA discovery structure"""
        return (
            f"{sensor_name} ({self._logger_descriptions[self._logger_index]})"
            if self._multi_inverter_logger_count
            else sensor_name
        )

    def publish_sensor_information(self, topic: str, observation: Observation):
        """Send HA discovery messages about available sensors

        Args:
            topic (str): MQTT topic for the sensor value
            observation (Observation): Sensor values
        """
        mqtt_topic_suffix = observation.sensor.mqtt_topic_suffix
        sensor_name = observation.sensor.name
        node_id = f"{self.component_prefix}_{self._logger_serial}"

        device_class, platform = self._get_device_class(mqtt_topic_suffix)

        kwargs: dict[str, Any] = {
            "availability_topic": f"{self._config.mqtt.topic_prefix}/status",
            "node_id": node_id,
            "unique_id": self._get_unique_id(sensor_name, mqtt_topic_suffix),
        }

        if self.expire_after is not None:
            kwargs["expire_after"] = self.expire_after

        if mqtt_topic_suffix == "settings/system_time":
            kwargs["value_template"] = "{{ as_datetime(value) }}"

        if platform == "binary_sensor":
            kwargs["payload_on"], kwargs["payload_off"] = self._get_payload_on_off(
                mqtt_topic_suffix
            )
        elif device_class == "enum":
            kwargs["options"] = self._get_options(mqtt_topic_suffix)
        elif device_class and device_class != "timestamp":
            kwargs["state_class"] = self._get_state_class(mqtt_topic_suffix)
            kwargs["unit"] = observation.sensor.unit

        self._send_discovery_message(
            self._fmt_sensor_name(sensor_name),
            mqtt_topic_suffix,
            state_topic=topic,
            platform=platform,
            device_class=device_class,
            device_type="inverter",
            **kwargs,
        )

    def publish_active_power_regulation(self):
        """Publish a HA number entity for active power regulation (0–120 %).

        Requires DEYE_FEATURE_ACTIVE_POWER_REGULATION=true.
        """
        availability_topic = f"{self._config.mqtt.topic_prefix}/status"
        command_topic = (
            f"{self._config.mqtt.topic_prefix}/settings/active_power_regulation/command"
        )
        state_topic = (
            f"{self._config.mqtt.topic_prefix}/settings/active_power_regulation"
        )
        node_id = f"{self.component_prefix}_{self._logger_serial}"
        _device_class, platform = self._get_device_class("active_power_regulation")
        self._send_discovery_message(
            self._fmt_sensor_name("Active Power Regulation"),
            "active_power_regulation",
            state_topic=state_topic,
            platform=platform,
            availability_topic=availability_topic,
            command_topic=command_topic,
            device_type="inverter",
            max=120,
            min=0,
            mode="slider",
            node_id=node_id,
            step=1,
            unit_of_measurement="%",
            unique_id=self._get_unique_id("", "settings/active_power_regulation"),
        )

    def publish_multi_inverter_data_aggregator(self):
        """Send a HA discovery message for the data aggregation feature"""

        for sensor_name, mqtt_topic_suffix, unit in [
            ("Aggregated daily energy", "day_energy", "kWh"),
            ("Aggregated AC active power", "ac/active_power", "W"),
        ]:
            device_class, platform = self._get_device_class(mqtt_topic_suffix)
            self._send_discovery_message(
                sensor_name,
                mqtt_topic_suffix,
                state_topic=f"{self._config.mqtt.topic_prefix}/{mqtt_topic_suffix}",
                platform=platform,
                device_class=device_class,
                unit=unit,
                availability_topic=f"{self._config.mqtt.topic_prefix}/status",
            )

    def publish_single_inverter_status(self):
        """Send HA discovery messages about the application and logger status"""
        for sensor_name, mqtt_topic_suffix, state_topic in [
            ("MQTT bridge", "application_status", "status"),
            ("Inverter logger", "logger_status", "logger_status"),
        ]:
            device_class, platform = self._get_device_class(mqtt_topic_suffix)
            self._send_discovery_message(
                self._fmt_sensor_name(sensor_name),
                mqtt_topic_suffix,
                state_topic=f"{self._config.mqtt.topic_prefix}/{state_topic}",
                platform=platform,
                device_class=device_class,
                entity_category="diagnostic",
                node_id=f"{self.component_prefix}_{self._config.logger.serial_number}",
                payload_on="online",
                payload_off="offline",
                unique_id=self._get_unique_id("", mqtt_topic_suffix),
            )

    def publish_multi_inverter_status(self):
        """\
        Send HA discovery messages about the application and logger status

        These status messages will be placed in a separate MQTT device in
        the diagnostic category.
        """
        all_states = [
            ("MQTT bridge", "application_status", "status"),
        ]
        for i in range(1, self._multi_inverter_logger_count + 1):
            desc = f"Logger ({self._get_logger_desc(i)})"
            all_states.append((desc, f"logger_status_{i}", f"{i}/logger_status"))
        for sensor_name, mqtt_topic_suffix, state_topic in all_states:
            device_class, platform = self._get_device_class(mqtt_topic_suffix)
            self._send_discovery_message(
                sensor_name,
                mqtt_topic_suffix,
                state_topic=f"{self._config.mqtt.topic_prefix}/{state_topic}",
                platform=platform,
                device_class=device_class,
                entity_category="diagnostic",
                payload_on="online",
                payload_off="offline",
            )

    def _send_discovery_message(
        self,
        sensor_name: str,
        mqtt_topic_suffix: str,
        state_topic: str,
        platform: str = "sensor",
        device_class: str | None = None,
        device_type: str = "bridge",
        **kwargs,
    ):
        """Build and publish a HA discovery config message.

        node_id, unique_id, and unit are consumed from kwargs;
        the rest is passed through to the payload verbatim.
        device_type selects the device map: "inverter" or "bridge" (default).
        """
        self._log.debug("Create HA discovery for %s", mqtt_topic_suffix)

        if not device_class and platform == "sensor":
            self._log.error(
                "Unable to determine device_class for topic %s on platform %s",
                mqtt_topic_suffix,
                platform,
            )
            return

        node_id = kwargs.pop("node_id", self.component_prefix)
        object_id = self._topic_to_object_id(mqtt_topic_suffix)
        unique_id = kwargs.pop("unique_id", object_id)
        if device_type == "bridge":
            identifier = f"{self.component_prefix}_bridge"
            device = self._get_discovery_device_map_bridge(identifier)
        else:
            identifier = f"{self.component_prefix}_{self._logger_serial}"
            device = self._get_discovery_device_map_inverter(identifier)

        # discovery topic format:
        # <discovery_prefix>/<component>/[<node_id>/]<object_id>/config
        discovery_topic = (
            f"{self.ha_discovery_prefix}/{platform}/{node_id}/{object_id}/config"
        )

        discovery_config = {
            "name": sensor_name,
            "device": device,
            "force_update": True,
            "state_topic": state_topic,
            "unique_id": unique_id,
        }

        if device_class:
            discovery_config["device_class"] = device_class
        if "unit" in kwargs:
            unit = kwargs.pop("unit")
            if unit == "minutes":  # Map units from deye-inverter-mqtt to Home Assistant
                unit = "min"
            discovery_config["unit_of_measurement"] = unit

        discovery_config.update(kwargs)

        payload = json.dumps(discovery_config)
        self._mqtt_client.publish(discovery_topic, payload)

    def process(self, events: DeyeEventList):
        """Create new HA discovery topics for all events"""

        _logger_index = events.logger_index
        self._logger_index = _logger_index
        _config_idx = (
            _logger_index - 1 if self._multi_inverter_logger_count else _logger_index
        )
        self._logger_serial = self._config.logger_configs[_config_idx].serial_number
        self._log.info(
            "Processing events from logger: %s, SN:%s",
            _logger_index,
            self._logger_serial,
        )
        self._device_name = self._fmt_sensor_name(
            f"{self.inverter_manufacturer} Inverter MQTT"
        )

        # publish status and aggregated data only once for all inverters
        if self._multi_inverter_logger_count == _logger_index:
            if self._multi_inverter_logger_count:
                self.publish_multi_inverter_status()
                if self._multi_inverter_data_aggregator_enabled:
                    self.publish_multi_inverter_data_aggregator()
            else:
                self.publish_single_inverter_status()

        if self._active_power_regulation_enabled:
            self.publish_active_power_regulation()

        event: DeyeObservationEvent
        for event in events:
            if not isinstance(event, DeyeObservationEvent):
                continue

            if not event.observation.sensor.mqtt_topic_suffix:
                continue

            if self._ignore_topic(
                event.observation.sensor.mqtt_topic_suffix,
                self._ignore_topic_patterns,
            ):
                continue

            topic = self._mqtt_client.build_topic_name(
                _logger_index, event.observation.sensor.mqtt_topic_suffix
            )
            self.publish_sensor_information(topic, event.observation)


class DeyePlugin:
    """Plugin entrypoint

    The plugin loader first instantiates the DeyePlugin class and then gets event processors from it.
    """

    def __init__(self, plugin_context: DeyePluginContext):
        """Initializes the plugin

        Args:
            plugin_context (DeyePluginContext): provides access to core service components, e.g. config
        """
        _log = logging.getLogger(DeyePlugin.__name__)
        self.publisher = None

        if not DeyeEnv.string("DEYE_HA_PLUGIN_HA_MQTT_PREFIX", None):
            _log.info(
                "Missing config item DEYE_HA_PLUGIN_HA_MQTT_PREFIX - do not instantiate "
                "DeyeHADiscovery plugin"
            )
            return

        _log.info("Instantiate DeyeHADiscovery plugin")
        self.publisher = DeyeHADiscovery(plugin_context)

    def get_event_processors(self) -> list[Any]:
        """Provides a list of custom event processors"""
        if self.publisher:
            return [self.publisher]
        return []
