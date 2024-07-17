# Copyright 2020 Red Hat, Inc.
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import json
import os
import subprocess
import time
import yaml

from ansible.module_utils.basic import AnsibleModule


ANSIBLE_METADATA = {
    'metadata_version': '1.0',
    'status': ['preview'],
    'supported_by': 'community'
}

DOCUMENTATION = """
---
module: edpm_os_net_config
author:
    - OpenStack EDPM Contributors
version_added: '1.0'
short_description: Execute os-net-config tool.
notes: []
requirements:
  - os-net-config
description:
    - Configure host network interfaces using a JSON config file format.
options:
  cleanup:
    description:
      - Cleanup unconfigured interfaces.
    type: bool
    default: false
  config_file:
    description:
      - Path to the configuration file.
    type: str
    default: /etc/os-net-config/config.yaml
  debug:
    description:
      - Print debug output.
    type: bool
    default: false
  detailed_exit_codes:
    description:
      - If enabled an exit code of '2' means that files were modified.
    type: bool
    default: false
  safe_defaults:
    description:
      - If enabled, safe defaults (DHCP for all interfaces) will be applied in
        case of failing while applying the provided net config.
    type: bool
    default: false
  use_nmstate:
    description:
      - If enabled, use nmstate and network manager for network configuration.
    type: bool
    default: false
"""

EXAMPLES = """
- name: Create network configs with defaults
  edpm_os_net_config:
"""

RETURN = """
rc:
  description:
    - Integer for the return code
  returned: always
  type: int
stdout:
  description:
    - The command standard output
  returned: always
  type: str
stderr:
  description:
    - The command standard error
  returned: always
  type: str
"""

DEFAULT_CFG = '/etc/os-net-config/dhcp_all_interfaces.yaml'


def _run_os_net_config(config_file, cleanup=False, debug=False,
                       detailed_exit_codes=False, noop=False,
                       use_nmstate=False):
    # Build os-net-config command
    argv = ['os-net-config --config-file {}'.format(config_file)]
    if cleanup:
        argv.append('--cleanup')
    if debug:
        argv.append('--debug')
    if detailed_exit_codes:
        argv.append('--detailed-exit-codes')
    if noop:
        argv.append('--noop')
    if use_nmstate:
        argv.append('--provider nmstate')
    else:
        argv.append('--provider ifcfg')
    cmd = " ".join(argv)

    # Apply the provided network configuration
    run = subprocess.run(cmd, shell=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True)
    return cmd, run


def _apply_safe_defaults(debug=False, noop=False, use_nmstate=False):
    _generate_default_cfg()
    cmd, run = _run_os_net_config(config_file=DEFAULT_CFG, cleanup=True,
                                  debug=debug, detailed_exit_codes=True,
                                  noop=noop, use_nmstate=use_nmstate)
    return cmd, run


def _generate_default_cfg():
    with open(DEFAULT_CFG, "w") as config_file:
        config_file.write('# autogenerated safe defaults file which'
                          'will run dhcp on discovered interfaces\n\n')
    network_interfaces = []
    for i in os.listdir('/sys/class/net/'):
        excluded_ints = ['lo', 'vnet']
        int_subdir = '/sys/class/net/{}/'.format(i)

        if i in excluded_ints or not os.path.isdir(int_subdir):
            continue
        with open('/sys/class/net/{}/addr_assign_type'.format(i), 'r') as f:
            mac_addr_type = int(f.read().strip())

        if mac_addr_type != 0:
            print('Device {} has generated MAC, skipping.'.format(i))
            continue
        if os.path.exists('/sys/class/net/{}/device/physfn'.format(i)):
            print("Device ({}) is a SR-IOV VF, skipping.".format(i))
            continue
        retries = 10
        has_link = _has_link(i)
        while has_link and retries > 0:
            cmd = 'ip link set dev {} up &>/dev/null'.format(i)
            subprocess.run(cmd, shell=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
            has_link = _has_link(i)
            if has_link:
                break
            time.sleep(1)
            retries -= 1
        if has_link:
            network_interface = {
                'type': 'interface',
                'name': i,
                'use_dhcp': True
            }
            network_interfaces.append(network_interface)

    network_config = {'network_config': network_interfaces}
    with open(DEFAULT_CFG, "ab") as config_file:
        config_file.write(json.dumps(network_config, indent=2).encode('utf-8'))


def _has_link(interface):
    try:
        with open('/sys/class/net/{}/carrier'.format(interface)) as f:
            has_link = int(f.read().strip())
    except FileNotFoundError:
        return False
    return has_link == 1


def main():
    module = AnsibleModule(
        argument_spec=yaml.safe_load(DOCUMENTATION)['options'],
        supports_check_mode=True,
    )
    results = dict(
        changed=False
    )
    # parse args
    args = module.params

    # Set parameters
    cleanup = args['cleanup']
    config_file = args['config_file']
    debug = args['debug']
    detailed_exit_codes = args['detailed_exit_codes']
    safe_defaults = args['safe_defaults']
    use_nmsate = args['use_nmstate']
    return_codes = [0]
    if detailed_exit_codes:
        return_codes.append(2)

    # Apply the provided network configuration
    cmd, run = _run_os_net_config(config_file, cleanup, debug,
                                  detailed_exit_codes,
                                  module.check_mode,
                                  use_nmstate=use_nmsate)
    results['stderr'] = run.stderr
    results['stdout'] = run.stdout
    if run.returncode not in return_codes and not module.check_mode:
        results['failed'] = True
        results['rc'] = run.returncode
        results['msg'] = ("Running %s failed with return code %s." % (
            cmd, run.returncode))
        if safe_defaults:
            module.warn("Error applying the provided network configuration, "
                        "safe defaults will be applied in best effort.")
            # Best effort to restore safe networking defaults to allow
            # an operator to ssh the node and debug if needed.
            _apply_safe_defaults(debug, module.check_mode,
                                 use_nmstate=use_nmsate)
    else:
        results['rc'] = 0
        results['msg'] = ("Successfully run %s." % cmd)
    if run.returncode == 2 and detailed_exit_codes:
        # NOTE: dprince this udev rule can apparently leak DHCP processes?
        # https://bugs.launchpad.net/tripleo/+bug/1538259
        # until we discover the root cause we can simply disable the
        # rule because networking has already been configured at this point
        udev_file = '/etc/udev/rules.d/99-dhcp-all-interfaces.rules'
        if os.path.isfile(udev_file):
            os.remove(udev_file)
        results['changed'] = True
    module.exit_json(**results)


if __name__ == '__main__':
    main()
