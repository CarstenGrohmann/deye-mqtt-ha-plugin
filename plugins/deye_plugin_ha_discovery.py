# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http:#www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

# Copyright (c) 2024 Carsten Grohmann

import fnmatch
import functools
import json
import logging
import re

from deye_config import DeyeConfig, DeyeEnv
from deye_events import DeyeEventProcessor, DeyeEventList, DeyeObservationEvent
from deye_mqtt import DeyeMqttClient
from deye_observation import Observation
from deye_plugin_loader import DeyePluginContext


RELEASE_DATE = "2024-10-22"


class DeyeHADiscovery(DeyeEventProcessor):
    """Plugin for HA discovery topics"""

    _active_power_regulation_enabled: bool = False
    """Publish control for active power regulation"""

    _ignore_topic_patterns: list = []
    """List of topics to be ignored by this plugin"""

    inverter_manufacturer: str | None = None
    """Inverter manufacturer"""

    inverter_model: str | None = None
    """Inverter model"""

    component_prefix = "deye_inverter_mqtt"
    """Prefix for the component name"""

    ha_discovery_prefix: str | None
    """MQTT prefix used by homeassistant"""

    def __init__(self, plugin_context: DeyePluginContext):
        self._config: DeyeConfig = plugin_context.config
        self._logging = logging.getLogger(DeyeHADiscovery.__name__)
        self._mqtt_client: DeyeMqttClient = plugin_context.mqtt_client
        self._logger_index: int | None = None
        self._logger_serial: str = ""
        self._device_name: str | None = None
        self._ignore_topic_patterns = []
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
            self._ignore_topic_patterns = value.split(":")
        else:
            self._ignore_topic_patterns = []

    def get_id(self):
        return f"HA Discovery Plugin version {RELEASE_DATE}"

    def __build_topic_name(self, logger_topic_prefix: str, topic_suffix: str) -> str:
        if logger_topic_prefix:
            return (
                f"{self._config.mqtt.topic_prefix}/{logger_topic_prefix}/{topic_suffix}"
            )
        else:
            return f"{self._config.mqtt.topic_prefix}/{topic_suffix}"

    def __map_logger_index_to_topic_prefix(self, logger_index: int):
        return str(logger_index) if logger_index > 0 else ""

    def _create_full_mqtt_topic(self, mqtt_topic_suffix: str) -> str:
        """Extend MQTT suffix to a full MQTT topic

        Args:
            mqtt_topic_suffix (str): Sensor specific part of the MQTT topic

        Returns:
            str: Full MQTT topic
        """
        logger_topic_prefix = self.__map_logger_index_to_topic_prefix(
            self._logger_index
        )
        topic = self.__build_topic_name(logger_topic_prefix, mqtt_topic_suffix)
        return topic

    @staticmethod
    def _adapt_unit(unit: str):
        """Map units from deye-inverter-mqtt to Home Assistant"""
        if unit == "minutes":
            unit = "min"
        return unit

    @staticmethod
    @functools.cache
    def _fmt_topic(topic: str) -> str:
        """Format topic to include into another topic string"""
        res = topic.lower()
        res = res.replace("/", "_")
        res = res.strip()
        return res

    @functools.cache
    def _get_unique_id(self, sensor_name: str) -> str:
        """Return a unique id for the current sensor"""
        # Do not change the prefix, as a changed unique ID generates new sensors.
        # The prefix differs from self.component_prefix = "deye_inverter_mqtt"
        _unique_id = f"deye_mqtt_inverter_{self._config.logger.serial_number}_{sensor_name}".lower()
        _unique_id = _unique_id.replace(" ", "_")
        return _unique_id

    @staticmethod
    @functools.cache
    def _get_device_class(topic: str) -> str:
        """Return device_class based on a given topic

        Args:
            topic (str): MQTT topic for the sensor value
        """
        device_class = ""

        # topic: ac/l*/voltage
        # topic: dc/pv*/voltage
        if topic.endswith("/voltage"):
            device_class = "voltage"

        # topic: ac/l*/current
        # topic: dc/pv*/current
        elif topic.endswith("/current"):
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

        # topic: battery/soc
        elif topic == "battery/soc":
            device_class = "battery"

        elif topic == "uptime":
            device_class = "duration"

        # topic: ac/temperature
        # topic: battery/temperature
        # topic: radiator_temp
        elif topic.endswith("temperature") or topic == "radiator_temp":
            device_class = "temperature"

        return device_class

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
        if (
            topic.endswith("_charge")
            or topic.endswith("_discharge")
            or topic.endswith("_energy")
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

    def publish_sensor_information(self, topic: str, observation: Observation):
        """Send HA discovery messages about available sensors

        Args:
            topic (str): MQTT topic for the sensor value
            observation (Observation): Sensor values
        """
        mqtt_topic_suffix = observation.sensor.mqtt_topic_suffix
        self._logging.debug("Create HA discovery for %s", mqtt_topic_suffix)

        device_class = self._get_device_class(mqtt_topic_suffix)
        if not device_class:
            self._logging.error(
                "Unable to determinate device_class for topic %s", mqtt_topic_suffix
            )
            return

        state_class = self._get_state_class(mqtt_topic_suffix)

        discovery_prefix = self.ha_discovery_prefix
        node_id = f"{self.component_prefix}_{self._config.logger.serial_number}"
        object_id = self._fmt_topic(mqtt_topic_suffix)

        # discovery topic format:
        # <discovery_prefix>/<component>/[<node_id>/]<object_id>/config
        discovery_topic = f"{discovery_prefix}/sensor/{node_id}/{object_id}/config"

        discover_config = {
            "name": observation.sensor.name,
            "unique_id": self._get_unique_id(observation.sensor.name),
            "force_update": True,
            "device_class": device_class,
            "state_class": state_class,
            "unit_of_measurement": self._adapt_unit(observation.sensor.unit),
            "availability_topic": f"{self._config.mqtt.topic_prefix}/status",
            "state_topic": topic,
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

    def publish_active_power_regulation(self):
        """Send HA discovery message for active power regulation feature"""
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
            "unique_id": self._get_unique_id("Active Power Regulation"),
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
                "unique_id": self._get_unique_id(mqtt_topic),
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

            if any(
                fnmatch.fnmatch(event.observation.sensor.mqtt_topic_suffix, pattern)
                for pattern in self._ignore_topic_patterns
            ):
                continue

            topic = self._create_full_mqtt_topic(
                event.observation.sensor.mqtt_topic_suffix
            )
            self.publish_sensor_information(topic, event.observation)


class DeyePlugin:
    """Plugin entrypoint

    The plugin loader first instantiates DeyePlugin class, and then gets event processors from it.
    """

    def __init__(self, plugin_context: DeyePluginContext):
        """Initializes the plugin

        Args:
            plugin_context (DeyePluginContext): provides access to core service components, e.g. config
        """
        _log = logging.getLogger(DeyePlugin.__name__)
        if DeyeEnv.string("DEYE_HA_PLUGIN_HA_MQTT_PREFIX", None):
            _log.info("Instantiate DeyeHADiscovery plugin")
            self.publisher = DeyeHADiscovery(plugin_context)
        else:
            _log.info(
                "Config item DEYE_HA_PLUGIN_HA_MQTT_PREFIX not set - do not instantiate DeyeHADiscovery plugin"
            )

    def get_event_processors(self) -> [DeyeEventProcessor]:
        """Provides a list of custom event processors"""
        if self.publisher:
            return [self.publisher]
        else:
            return []
