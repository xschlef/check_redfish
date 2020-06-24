# -*- coding: utf-8 -*-
#  Copyright (c) 2020 Ricardo Bartels. All rights reserved.
#
#  check_redfish.py
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see file LICENSE.txt included in this
#  repository or visit: <https://opensource.org/licenses/MIT>.

from cr_module.classes.inventory import System, Chassi
from cr_module.common import get_status_data, grab


def get_system_info(plugin_object):
    plugin_object.set_current_command("System Info")

    systems = plugin_object.rf.get_system_properties("systems")

    if systems is None or len(systems) == 0:
        plugin_object.add_output_data("UNKNOWN", "No 'systems' property found in root path '/redfish/v1'")
        return

    for system in systems:
        get_single_system_info(plugin_object, system)

    # add chassi inventory here too
    if plugin_object.cli_args.inventory is True:

        for chassi in plugin_object.rf.get_system_properties("chassis") or list():
            get_single_chassi_info(plugin_object, chassi)

    return


def get_single_system_info(plugin_object, redfish_url):
    system_response = plugin_object.rf.get(redfish_url)

    if system_response is None:
        plugin_object.add_output_data("UNKNOWN", f"No system information data returned for API URL '{redfish_url}'")
        return

    # get model data
    model = system_response.get("Model")
    # Huawei system
    if plugin_object.rf.vendor == "Huawei":
        model = grab(system_response, f"Oem.{plugin_object.rf.vendor_dict_key}.ProductName")

    # get memory size
    mem_size = grab(system_response, "MemorySummary.TotalSystemMemoryGiB")

    # Dell system
    # just WHY?
    if plugin_object.rf.vendor == "Dell" and mem_size is not None and int(mem_size) % 1024 != 0:
        mem_size = round(mem_size * 1024 ** 3 / 1000 ** 3)

    status_data = get_status_data(system_response.get("Status"))

    system_inventory = System(
        id=system_response.get("Id"),
        name=system_response.get("Name"),
        manufacturer=system_response.get("Manufacturer"),
        serial=system_response.get("SerialNumber"),
        health_status=status_data.get("Health"),
        operation_status=status_data.get("State"),
        power_state=system_response.get("PowerState"),
        bios_version=system_response.get("BiosVersion"),
        host_name=system_response.get("HostName"),
        indicator_led=system_response.get("IndicatorLED"),
        cpu_num=grab(system_response, "ProcessorSummary.Count"),
        part_number=system_response.get("PartNumber"),
        mem_size=mem_size,
        model=model,
        type=system_response.get("SystemType")
    )

    if plugin_object.cli_args.verbose:
        system_inventory.source_data = system_response

    # add relations
    system_inventory.add_relation(plugin_object.rf.get_system_properties(), system_response.get("Links"))

    plugin_object.inventory.add(system_inventory)

    host_name = "NOT SET"
    if system_inventory.host_name is not None and len(system_inventory.host_name) > 0:
        host_name = system_inventory.host_name

    status_text = f"Type: {system_inventory.manufacturer} {system_inventory.model} " \
                  f"(CPU: {system_inventory.cpu_num}, MEM: {system_inventory.mem_size}GB) - " \
                  f"BIOS: {system_inventory.bios_version} - " \
                  f"Serial: {system_inventory.serial} - " \
                  f"Power: {system_inventory.power_state} - Name: {host_name}"

    plugin_object.add_output_data(
        "CRITICAL" if system_inventory.health_status not in ["OK", "WARNING"] else system_inventory.health_status,
        status_text, summary=not plugin_object.cli_args.detailed)

    if plugin_object.cli_args.detailed is True:

        # add ILO data
        if plugin_object.rf.vendor == "HPE":
            plugin_object.add_output_data("OK", "%s - FW: %s" %
                                          (plugin_object.rf.vendor_data.ilo_version,
                                           plugin_object.rf.vendor_data.ilo_firmware_version))
        # add SDCard status
        if plugin_object.rf.vendor == "Fujitsu":
            sd_card = plugin_object.rf.get(redfish_url + "/Oem/ts_fujitsu/SDCard")

            if sd_card.get("Inserted") is True:
                sd_card_status = sd_card.get("Status")
                sd_card_capacity = sd_card.get("CapacityMB")
                sd_card_free_space = sd_card.get("FreeSpaceMB")

                status_text = f"SDCard Capacity {sd_card_capacity}MB and {sd_card_free_space}MB free space left."
                plugin_object.add_output_data("CRITICAL" if sd_card_status not in ["OK", "WARNING"] else sd_card_status,
                                              status_text)

    return


def get_single_chassi_info(plugin_object, redfish_url):
    chassi_response = plugin_object.rf.get(redfish_url)

    # get status data
    status_data = get_status_data(chassi_response.get("Status"))

    chassi_inventory = Chassi(
        id=chassi_response.get("Id"),
        name=chassi_response.get("Name"),
        manufacturer=chassi_response.get("Manufacturer"),
        serial=chassi_response.get("SerialNumber"),
        health_status=status_data.get("Health"),
        operation_status=status_data.get("State"),
        sku=chassi_response.get("SKU"),
        indicator_led=chassi_response.get("IndicatorLED"),
        model=chassi_response.get("Model"),
        type=chassi_response.get("ChassisType")
    )

    if plugin_object.cli_args.verbose:
        chassi_inventory.source_data = chassi_response

    # add relations
    chassi_inventory.add_relation(plugin_object.rf.get_system_properties(), chassi_response.get("Links"))

    plugin_object.inventory.add(chassi_inventory)

    return

# EOF
