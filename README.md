# Deye MQTT HA Plugin

## Introduction

This plugin enables Home Assistant to automatically discover all values published by the "Deye Solar Inverter
MQTT Bridge".

Energy usage diagram provided by Home Assistant filled with data from a "Deye solar inverter MQTT bridge":

![Screenshot of energy usage diagram provided by Home Assistant filled with data from a Deye solar inverter](./screenshot_energy_usage.png)

This integration can read and update the active power regulation feature's value if it's enabled. However, reading and
updating the "time of use" (ToU) configuration has not been implemented yet.

## Installation

1. Install Deye solar inverter MQTT bridge

2. Expand the Deye Solar Inverter MQTT Bridge configuration file `config.env` with a customized version of this
   section:

    ```bash
    # Home Assistant Integration
    # ==========================
    PLUGINS_ENABLED=deye_plugin_ha_discovery
    DEYE_HA_PLUGIN_HA_MQTT_PREFIX=homeassistant
    DEYE_HA_PLUGIN_INVERTER_MANUFACTURER=<your manufacturer>
    DEYE_HA_PLUGIN_INVERTER_MODEL=<your inverter>
    # Topics not published to HA
    # Use : as separator, supports Unix shell-style wildcards *, ?, [seq] and
    # [!seq] as implemented with Python fnmatch,
    DEYE_HA_PLUGIN_IGNORE_TOPIC_PATTERNS=uptime:*/pv[234]/*
    ```

3. Install the plugin from `plugins` directory as described in ["How to start the docker container with custom plugins"](https://github.com/kbialek/deye-inverter-mqtt#how-to-start-the-docker-container-with-custom-plugins) and restart container to
   activate the changes in `config.env`.

4. Switch to the Home Assistant and install [Utility Meter](https://www.home-assistant.io/integrations/utility_meter/)

5. Configure a Utility Meter helper to reset the daily production counter at midnight.

    ```bash
    # Example configuration.yaml entry
    utility_meter:
      energy:
        name: "Production total (daily reset)"
        source: sensor.deye_inverter_mqtt_production_total
        cycle: daily
    ```

    or graphically

    ![Screenshot of Utility Meter setup part 1](./screenshot_setup_utility_meter_1.png)

    ![Screenshot of Utility Meter setup part 2](./screenshot_setup_utility_meter_2.png)

## Requirements

* [Deye solar inverter MQTT bridge](https://github.com/kbialek/deye-inverter-mqtt) version 2024.11.1 or newer

## Troubleshooting

1. Ensure that the container `deye-mqtt` has the required minimum version

2. Check if the plugin has been loaded.

    ```bash
    docker logs deye-mqtt
    ```

   When starting the container after loading this plugin, a message similar to the following line should appear in
   the container log.

    ```
    DeyePluginLoader - INFO - Loading plugin: 'deye_plugin_ha_discovery'
    ```

    If this line does not appear, you should check the installation of the plugin.

3. Check log of the `deye-mqtt` container for errors.

    On demand increase the detail of the logging in `config.env` to `LOG_LEVEL=DEBUG` and restart the container.

4. Checking the content published in the MQTT broker. You can use a graphical tool such as the [MQTT Explorer](https://mqtt-explorer.com/) for this.


## Resources

* [Project Page](https://carstengrohmann.de/deye-mqtt-ha-plugin.html)
* [Source Code](https://git.sr.ht/~carstengrohmann/deye-mqtt-ha-plugin)
  (mirrored on [GitHub](https://github.com/CarstenGrohmann/deye-mqtt-ha-plugin))
* [Home Assistent](https://www.home-assistant.io/)
* [Deye solar inverter MQTT bridge](https://github.com/kbialek/deye-inverter-mqtt)

## Changelog

### 2024-XX-XX
* Add support for active power regulation
* Internal code changes
* Use new public API to create MQTT topic
  (requires [Deye solar inverter MQTT bridge](https://github.com/kbialek/deye-inverter-mqtt)
  at least version 2024.11.1)
 
### 2024-10-22
* Add device classes for total_energy_bought and daily_energy_sold

### 2024-10-03
* Fix wrong unit for uptime sensor

### 2024-09-20
* README extended
* All energy topics uses state class "total_increasing" now
* Add more MQTT topics

### 2024-08-30
* first release

## Known Bugs/Issues

Check the project [issue tracker](https://todo.sr.ht/~carstengrohmann/deye-mqtt-ha-plugin)
for current open bugs. New bugs can be reported there also.

## License

This project is licensed under the Apache 2.0 license.

> Copyright (c) 2024 Carsten Grohmann,  mail &lt;add at here&gt; carstengrohmann.de
>
> Licensed to the Apache Software Foundation (ASF) under one
> or more contributor license agreements.  See the NOTICE file
> distributed with this work for additional information
> regarding copyright ownership.  The ASF licenses this file
> to you under the Apache License, Version 2.0 (the
> "License"); you may not use this file except in compliance
> with the License.  You may obtain a copy of the License at
>
>   http://www.apache.org/licenses/LICENSE-2.0
>
> Unless required by applicable law or agreed to in writing,
> software distributed under the License is distributed on an
> "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
> KIND, either express or implied.  See the License for the
> specific language governing permissions and limitations
> under the License.

Enjoy!

Carsten Grohmann
