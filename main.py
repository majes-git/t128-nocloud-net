#!/usr/bin/env python3

import argparse
import collections.abc
import os
import pkgutil
import requests
import sys
import yaml
from flask import Flask, render_template, render_template_string
from jinja2 import Environment, FunctionLoader
from textwrap import indent

from templates.defaults import defaults

DEFAULT_DNS = [ '8.8.8.8', '1.1.1.1' ]
MAX_IP = 250

app = Flask(__name__)


def parse_arguments():
    """Get commandline arguments."""
    parser = argparse.ArgumentParser(
        description='Serve nocloud-net files for cloud-init')
    parser.add_argument('--deployment', '-d', required=True,
                        help='Deployment data URL')
    return parser.parse_args()


def load_template(vm_type):
    # if file cannot be found (type == None) return a 404
    data = None

    # first try to load a template from program directory (templates)
    # if not possible, try to load it from the pyz
    dir = os.path.join(os.path.dirname(sys.argv[0]), 'templates')
    filename = f'{vm_type}.yaml'
    if vm_type.endswith('.yaml'):
        filename = vm_type
    try:
        with open(os.path.join(dir, filename)) as fd:
            data = fd.read()
            return data
    except FileNotFoundError:
        pass

    # try to load template from pyz
    try:
        data = pkgutil.get_data('templates', filename).decode('utf8')
    except OSError:
        data = f'# Could not find a template for machine type: {vm_type}\n\n'
    return data


def load_deployment(url):
    r = requests.get(url)
    if r.status_code == 200:
        return yaml.safe_load(r.text)


def update(d, u):
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            d[k] = update(d.get(k, {}), v)
        else:
            d[k] = v
    return d


def get_network_config(deployment_config, vm_name, mgmt_ip):
    network_config = {
        'network': {
            'version': 2,
            'ethernets': {}
        }
    }

    for vm in deployment_config.get('vms'):
        if vm.get('name') == vm_name:
            vm_id = vm.get('id')
            network_config['network']['ethernets']['eth0'] = {
                 'addresses': [ mgmt_ip + '/24' ],
                 'gateway4': mgmt_ip_prefix.format(1),
                 'nameservers': {
                    'addresses': DEFAULT_DNS,
                 },
            }
            i = 1
            for network in vm.get('networks', []):
                interface = f'eth{i}'
                elements = network.split(',')
                if len(elements) == 1:
                    continue
                nc = {}
                for e in range(1, len(elements)):
                    if e == 1:
                        nc['addresses'] = [elements[1]]
                    if e == 2 and elements[2]:
                        nc['gateway4'] = elements[2]
                    if e == 3 and elements[3]:
                        nc['routes'] = []
                        for route in elements[3].split(';'):
                            destination, via = route.split('->')
                            nc['routes'].append({'to': destination, 'via': via})
                network_config['network']['ethernets'][interface] = nc
                i += 1
            return yaml.dump(network_config, sort_keys=False)


@app.route('/<hostname>/meta-data')
def meta(hostname):
        return f'''instance-id: {hostname}
local-hostname: {hostname}
'''


@app.route('/<hostname>/user-data')
def user(hostname):
    global mgmt_ip_prefix
    deployment_config = defaults
    update(deployment_config, load_deployment(app.config['DEPLOYMENT_URL']))
    mgmt_ip_prefix = deployment_config['global']['mgmt_ip_prefix'].strip('.') + '.{}'

    user_data = ''
    for vm in deployment_config.get('vms'):
        vm_name = vm.get('name')
        vm_type = vm.get('type')
        vm_id = vm.get('id')
        deployment_name = deployment_config['deployment']
        if hostname == f'{vm_name}' or hostname == f'{deployment_name}-{vm_name}':
            host_byte = vm_id
            if host_byte > MAX_IP:
                host_byte = host_byte % (MAX_IP - 1) + 2
            mgmt_ip = mgmt_ip_prefix.format(host_byte)
            network_config = indent(get_network_config(deployment_config, vm_name, mgmt_ip), ' '*4)
            if vm_type:
                conductor_ips = []
                try:
                    conductor_ips = [mgmt_ip_prefix.format(c) for c in deployment_config['global']['ssr']['conductor']]
                except KeyError:
                    pass
                template = env.get_template(vm_type)
                addition_variables = {
                    'conductor_ips': conductor_ips,
                }
                addition_variables.update(deployment_config['global'])
                if vm.get('template_variables'):
                    addition_variables.update(vm['template_variables'])
                if template:
                    user_data += template.render(
                        mgmt_ip=mgmt_ip,
                        deployment=deployment_name,
                        network_config=network_config,
                        pre_runcmd=vm.get('pre_runcmd'),
                        post_runcmd=vm.get('post_runcmd'),
                        write_files=vm.get('write_files'),
                        **addition_variables,
                    )
    return user_data


@app.route('/<hostname>/vendor-data')
def vendor(hostname):
        return ''


if __name__ == '__main__':
    global env
    env = Environment(loader=FunctionLoader(load_template), cache_size=0)
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    args = parse_arguments()
    app.config['DEPLOYMENT_URL'] = args.deployment
    app.run(host='0.0.0.0', port=8000)
