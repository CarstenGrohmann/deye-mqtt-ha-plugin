"""Tests for DeyeHADiscovery._get_device_class() and _get_state_class()."""

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

import pytest

from deye_plugin_ha_discovery import DeyeHADiscovery


@pytest.mark.parametrize(
    "topic,expected",
    [
        # voltage
        ("ac/l1/voltage", ("voltage", "sensor")),
        ("dc/pv1/voltage", ("voltage", "sensor")),
        ("bms/1/charging_voltage", ("voltage", "sensor")),
        ("bms/1/discharge_voltage", ("voltage", "sensor")),
        # current
        ("ac/l1/current", ("current", "sensor")),
        ("dc/pv1/current", ("current", "sensor")),
        ("bms/1/charge_current_limit", ("current", "sensor")),
        ("bms/1/discharge_current_limit", ("current", "sensor")),
        ("bms/1/charging_max_current", ("current", "sensor")),
        ("bms/1/discharge_max_current", ("current", "sensor")),
        ("settings/battery/maximum_charge_current", ("current", "sensor")),
        ("settings/battery/maximum_discharge_current", ("current", "sensor")),
        # power
        ("ac/active_power", ("power", "sensor")),
        ("ac/l1/power", ("power", "sensor")),
        ("dc/pv1/power", ("power", "sensor")),
        ("ac/l1/ct/internal", ("power", "sensor")),
        ("ac/l1/ct/external", ("power", "sensor")),
        # energy
        ("battery/daily_charge", ("energy", "sensor")),
        ("battery/total_discharge", ("energy", "sensor")),
        ("day_energy", ("energy", "sensor")),
        ("dc/pv1/total_energy", ("energy", "sensor")),
        ("ac/total_energy_bought", ("energy", "sensor")),
        # frequency
        ("ac/freq", ("frequency", "sensor")),
        ("ac/grid_frequency", ("frequency", "sensor")),
        # temperature
        ("ac/temperature", ("temperature", "sensor")),
        ("battery/temperature", ("temperature", "sensor")),
        ("bms/1/temp", ("temperature", "sensor")),
        ("radiator_temp", ("temperature", "sensor")),
        # battery / soc
        ("battery/soc", ("battery", "sensor")),
        ("bms/1/soc", ("battery", "sensor")),
        # special platforms and device classes
        ("active_power_regulation", (None, "number")),
        ("settings/system_time", ("timestamp", "sensor")),
        ("uptime", ("duration", "sensor")),
        ("inverter/status", ("enum", "sensor")),
        ("settings/workmode", ("enum", "sensor")),
        ("settings/solar_sell", (None, "binary_sensor")),
        ("ac/ongrid", ("power", "binary_sensor")),
        ("application_status", ("running", "binary_sensor")),
        ("logger_status", ("connectivity", "binary_sensor")),
        ("logger_status_1", ("connectivity", "binary_sensor")),
        # unknown topic: no device class, default sensor platform
        ("some/unknown/topic", (None, "sensor")),
    ],
)
def test_get_device_class_returns_expected_mapping(topic, expected):
    assert DeyeHADiscovery._get_device_class(topic) == expected


@pytest.mark.parametrize("topic", ["bms/1/temp", "bms/1/voltage", "bms/1/current"])
def test_get_device_class_does_not_treat_all_bms_topics_as_battery(topic):
    # regression for b8c9747: topic.startswith("bms/") wrongly forced
    # device_class "battery" on every BMS topic, not just .../soc
    device_class, _platform = DeyeHADiscovery._get_device_class(topic)
    assert device_class != "battery"


def test_get_device_class_settings_battery_grid_charge_is_binary_sensor():
    # settings/battery/grid_charge ends with "_charge"; the exact-match
    # binary_sensor branch must run before the energy suffix check
    assert DeyeHADiscovery._get_device_class("settings/battery/grid_charge") == (
        None,
        "binary_sensor",
    )


def test_get_device_class_requires_path_separator_before_charge_current_limit():
    # regression for b8c9747: matching the bare substring "charge_current_limit"
    # (no leading slash) wrongly classified unrelated topics as "current"
    device_class, _platform = DeyeHADiscovery._get_device_class("foo_charge_current_limit")
    assert device_class is None


@pytest.mark.parametrize(
    "topic,expected",
    [
        ("battery/daily_charge", "total_increasing"),
        ("battery/total_discharge", "total_increasing"),
        ("day_energy", "total_increasing"),
        ("total_energy", "total_increasing"),
        ("dc/pv1/day_energy", "total_increasing"),
        ("ac/total_energy_bought", "total_increasing"),
        ("ac/daily_energy_sold", "total_increasing"),
        ("settings/system_time", "total_increasing"),
        ("uptime", "total_increasing"),
        ("ac/l1/current", "measurement"),
        ("ac/active_power", "measurement"),
        ("battery/soc", "measurement"),
        ("radiator_temp", "measurement"),
    ],
)
def test_get_state_class_returns_expected_class(topic, expected):
    assert DeyeHADiscovery._get_state_class(topic) == expected


@pytest.mark.parametrize(
    "topic,expected",
    [
        ("settings/system_time", "{{ as_datetime(value) }}"),
        ("settings/workmode", '{{ ["selling_first", "zero_export_to_load", "zero_export_to_ct"][value | int] }}'),
        ("ac/active_power", None),
    ],
)
def test_get_value_template_returns_expected_template(topic, expected):
    assert DeyeHADiscovery._get_value_template(topic) == expected
