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

# Copyright (c) 2024,2025 Carsten Grohmann

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


RELEASE_DATE = "2025-04-17"


class DeyeHADiscovery(DeyeEventProcessor):
    """Plugin for HA discovery topics"""

    _active_power_regulation_enabled: bool = False
    """Publish control for active power regulation"""

    _ignore_user_topic_patterns: tuple = ()
    """List of user-specific topics to be ignored"""

    _ignore_default_topic_pattern: list[str] = ["settings/active_power_regulation"]
    """List of topics that are always ignored"""

    _use_topic_in_unique_id: bool = False
    """Use MQTT topic instead of sensor name in unique_id"""

    inverter_manufacturer: str | None = None
    """Inverter manufacturer"""

    inverter_model: str | None = None
    """Inverter model"""

    component_prefix = "deye_inverter_mqtt"
    """\
    Prefix for the component name.

    Use underscore to separate parts to avoid problems with the MQTT topic.
    """

    ha_discovery_prefix: str | None
    """MQTT prefix used by homeassistant"""

    expire_after: int | None = None
    """Expire_after parameter for HA sensors"""

    def __init__(self, plugin_context: DeyePluginContext):
        self._config: DeyeConfig = plugin_context.config
        self._logging = logging.getLogger(DeyeHADiscovery.__name__)
        self._mqtt_client: DeyeMqttClient = plugin_context.mqtt_client
        self._logger_index: int | None = None
        self._logger_serial: str = ""
        self._device_name: str | None = None
        self._ignore_user_topic_patterns = ()
        self._use_topic_in_unique_ids = False
        self._active_power_regulation_enabled = DeyeEnv.boolean(
            "DEYE_FEATURE_ACTIVE_POWER_REGULATION", False
        )

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
            self._ignore_user_topic_patterns = tuple(
                value.split(":") + self._ignore_default_topic_pattern
            )
        else:
            self._ignore_user_topic_patterns = tuple(self._ignore_default_topic_pattern)
        value = DeyeEnv.string("DEYE_HA_PLUGIN_EXPIRE_AFTER", "")
        if value:
            self.expire_after = int(value)
        self._use_topic_in_unique_id = DeyeEnv.boolean(
            "DEYE_HA_PLUGIN_USE_TOPIC_IN_UNIQUE_ID", False
        )

    def get_id(self):
        return f"HA Discovery Plugin version {RELEASE_DATE}"

    @staticmethod
    def _adapt_unit(unit: str):
        """Map units from deye-inverter-mqtt to Home Assistant"""
        if unit == "minutes":
            unit = "min"
        return unit

    @staticmethod
    @functools.cache
    def _fmt_topic(topic: str) -> str:
        """Format the topic to include in another topic string"""
        res = topic.lower()
        res = res.replace("/", "_")
        res = res.strip()
        return res

    @functools.cache
    def _get_unique_id(self, sensor_name: str, topic_name: str) -> str:
        """Return a unique id for the current sensor"""
        assert sensor_name or topic_name
        if self._use_topic_in_unique_id:
            prefix = self.component_prefix
        else:
            # Do not change the prefix, as a changed unique ID generates new sensors.
            # The prefix differs from self.component_prefix = "deye_inverter_mqtt"
            prefix = "deye_mqtt_inverter"
        if self._use_topic_in_unique_id and topic_name:
            component = topic_name
        else:
            component = sensor_name
        _unique_id = f"{prefix}_{self._config.logger.serial_number}_{component}".lower()
        _unique_id = _unique_id.replace(" ", "_")
        _unique_id = _unique_id.replace("/", "_")
        return _unique_id

    @staticmethod
    @functools.cache
    def _get_device_class(topic: str) -> tuple:
        """Return device_class and platform based on a given topic

        Args:
            topic (str): MQTT topic for the sensor value
        """
        device_class = ""
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
        elif (
            topic.endswith("/current")
            or topic.endswith("charge_current_limit")
            or topic.endswith("/charging_max_current")
            or topic.endswith("/discharge_max_current")
        ):
            device_class = "current"

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

        # topic: ac/active_power
        # topic: ac/l*/power
        # topic: dc/pv*/power
        # topic: dc/total_power
        # topic: operating_power
        elif topic.endswith("power"):
            device_class = "power"

        # topic: ac/freq
        elif topic.endswith("/freq"):
            device_class = "frequency"

        # topic: ac/temperature
        # topic: battery/temperature
        # topic: battery/*/temperature
        # topic: radiator_temp
        elif (
            topic.endswith("temperature")
            or topic.endswith("/temp")
            or topic == "radiator_temp"
        ):
            device_class = "temperature"

        # topic: battery/soc
        # topic: bms/*/soc
        elif topic.endswith("/soc") or topic.startswith("bms/"):
            device_class = "battery"

        elif topic == "uptime":
            device_class = "duration"

        elif topic == "inverter/status":
            device_class = "enum"

        elif topic == "ac/ongrid":
            device_class = "power"
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

    @staticmethod
    @functools.cache
    def _get_state_class(topic: str) -> str:
        """Return state_class based on a given topic

        Args:
            topic (str): MQTT topic for the sensor value
        """
        state_class = ""

        # topic: battery/(daily|total)_(charge|discharge)
        # topic: (day|total)_energy
        # topic: dc/pv*/(day|total)_energy
        # topic: uptime
        # topic: ac/(daily|total)_energy_(bought|sold)
        if (
            topic.endswith("_charge")
            or topic.endswith("_discharge")
            or topic.endswith("_energy")
            or topic.endswith("_energy_bought")
            or topic.endswith("_energy_sold")
            or topic == "uptime"
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
    def _get_options(topic: str) -> list | list[str]:
        """Return entity options based on a given topic

        Args:
            topic (str): MQTT topic for the sensor value
        """
        options = []
        if topic == "inverter/status":
            options = ["standby", "selfcheck", "normal", "alarm", "fault"]

        return options

    @staticmethod
    @functools.cache
    def _get_payload_on_off(topic: str) -> tuple:
        """Return payload_on and payload_off values for a binary sensor with a given topic

        Args:
            topic (str): MQTT topic for the sensor value
        """
        return "True", "False"

    def publish_sensor_information(self, topic: str, observation: Observation):
        """Send HA discovery messages about available sensors

        Args:
            topic (str): MQTT topic for the sensor value
            observation (Observation): Sensor values
        """
        mqtt_topic_suffix = observation.sensor.mqtt_topic_suffix
        self._logging.debug("Create HA discovery for %s", mqtt_topic_suffix)

        device_class, platform = self._get_device_class(mqtt_topic_suffix)
        if not device_class:
            self._logging.error(
                "Unable to determinate device_class for topic %s", mqtt_topic_suffix
            )
            return

        discovery_prefix = self.ha_discovery_prefix
        node_id = f"{self.component_prefix}_{self._config.logger.serial_number}"
        object_id = self._fmt_topic(mqtt_topic_suffix)

        # discovery topic format:
        # <discovery_prefix>/<component>/[<node_id>/]<object_id>/config
        discovery_topic = f"{discovery_prefix}/{platform}/{node_id}/{object_id}/config"

        discover_config = {
            "name": observation.sensor.name,
            "unique_id": self._get_unique_id(observation.sensor.name, mqtt_topic_suffix),
            "force_update": True,
            "device_class": device_class,
            "availability_topic": f"{self._config.mqtt.topic_prefix}/status",
            "state_topic": topic,
            "device": {
                "identifiers": [node_id],
                "name": self._device_name,
                "manufacturer": self.inverter_manufacturer,
                "model": f"{self.inverter_model} SN:{self._logger_serial}",
                "serial_number": str(self._logger_serial),
                "sw_version": f'{self.component_prefix.replace("_", "-")} with {self.get_id()}',
            },
        }

        if self.expire_after is not None:
            discover_config["expire_after"] = self.expire_after

        if platform == "binary_sensor":
            discover_config["payload_on"], discover_config["payload_off"] = (
                self._get_payload_on_off(mqtt_topic_suffix)
            )
        else:
            if device_class == "enum":
                discover_config["options"] = self._get_options(mqtt_topic_suffix)
            else:
                discover_config["state_class"] = self._get_state_class(
                    mqtt_topic_suffix
                )
                discover_config["unit_of_measurement"] = self._adapt_unit(
                    observation.sensor.unit
                )

        payload = json.dumps(discover_config)
        self._mqtt_client.publish(discovery_topic, payload)

    def publish_active_power_regulation(self):
        """Send a HA discovery message for active power regulation feature"""
        component_id = f"{self.component_prefix}_{self._config.logger.serial_number}"
        node_id = f"{self.component_prefix}_{self._config.logger.serial_number}"

        # discovery topic format:
        # <discovery_prefix>/<component>/[<node_id>/]<object_id>/config
        discovery_topic = (
            f"{self.ha_discovery_prefix}/number/{component_id}/"
            "active_power_regulation/config"
        )

        command_topic = (
            f"{self._config.mqtt.topic_prefix}/settings/active_power_regulation/command"
        )
        state_topic = (
            f"{self._config.mqtt.topic_prefix}/settings/active_power_regulation"
        )

        # TOPIC: {MQTT_TOPIC_PREFIX}/settings/active_power_regulation/command
        discover_config = {
            "name": "Active Power Regulation",
            "unique_id": self._get_unique_id("", "settings/active_power_regulation"),
            "unit_of_measurement": "%",
            "availability_topic": f"{self._config.mqtt.topic_prefix}/status",
            "min": 0,
            "max": 120,
            "mode": "slider",
            "step": 1,
            "command_topic": command_topic,
            "state_topic": state_topic,
            "device": {
                "identifiers": [node_id],
                "name": self._device_name,
                "manufacturer": self.inverter_manufacturer,
                "model": f"{self.inverter_model} SN:{self._logger_serial}",
                "serial_number": str(self._logger_serial),
                "sw_version": f"deye-inverter-mqtt with {self.get_id()}",
            },
        }
        payload = json.dumps(discover_config)
        self._mqtt_client.publish(discovery_topic, payload)

    def publish_status_information(self):
        """Send HA discovery messages about the application and logger status"""
        for name, mqtt_topic, device_class, state_topic in [
            ("MQTT bridge", "application_status", "running", "status"),
            ("Inverter logger", "logger_status", "connectivity", "logger_status"),
        ]:
            component_id = (
                f"{self.component_prefix}_{self._config.logger.serial_number}"
            )
            discovery_topic = f"{self.ha_discovery_prefix}/binary_sensor/{component_id}/{mqtt_topic}/config"

            discover_config = {
                "name": f"{name}",
                "device_class": device_class,
                "entity_category": "diagnostic",
                "force_update": True,
                "unique_id": self._get_unique_id("", mqtt_topic),
                "state_topic": f"{self._config.mqtt.topic_prefix}/{state_topic}",
                "payload_on": "online",
                "payload_off": "offline",
                "device": {
                    "identifiers": [component_id],
                    "name": self._device_name,
                    "manufacturer": self.inverter_manufacturer,
                    "model": f"{self.inverter_model} SN:{self._logger_serial}",
                    "serial_number": str(self._logger_serial),
                    "sw_version": f"deye-inverter-mqtt with {self.get_id()}",
                },
            }
            payload = json.dumps(discover_config)
            self._mqtt_client.publish(discovery_topic, payload)

    def process(self, events: DeyeEventList):
        """Create a new HA discovery topic for all events"""

        self._logger_index = events.logger_index
        self._logger_serial = self._config.logger_configs[
            self._logger_index
        ].serial_number
        self._logging.info(
            "Processing events from logger: %s, SN:%s",
            self._logger_index,
            self._logger_serial,
        )
        self._device_name = f"{self.inverter_manufacturer} Inverter MQTT"

        self.publish_status_information()

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
                self._ignore_user_topic_patterns,
            ):
                continue

            topic = self._mqtt_client.build_topic_name(
                self._logger_index, event.observation.sensor.mqtt_topic_suffix
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

        if DeyeEnv.integer("DEYE_LOGGER_COUNT", 0):
            _log.info(
                "Unsupported multi-inverter configuration found - do not instantiate "
                "DeyeHADiscovery plugin"
            )
            return
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
        else:
            return []
