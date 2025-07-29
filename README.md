# Insistent service launcher

[![molecule](https://github.com/raven428/ansible-mega-launch/actions/workflows/test-role.yaml/badge.svg)](https://github.com/raven428/ansible-mega-launch/actions/workflows/test-role.yaml)

The role performs a service launch or stop with a few of checks after with a few restarts or force kill in case of failure

## Start service

See [an example](molecule/default/converge.yaml#L38-L51) from role unit-test. This includes:

1. start `systemd` service named `service_name`
2. call [`check_service`](library/check_service.py) module
3. passed_checks became more or equal `required_checks`
4. repeat until retries exceed `check_retries`
5. then failure handled by rescue block
6. which stop service by `systemd`
7. rescue delay `rescue_delay` and
8. repeat 1-6 `max_rescues` times

If required checks didn't happen during numerous restarts of `systemd` service, the service will be stopped and role will fail

Second option (disabled by default) is start by [module](library/mega_launch.py) with altered [async](library/mega_status.py) [checks](action_plugins/mega_status.py) from Ansible sources. Example also [can be found](molecule/default/converge.yaml#L21-L36) in molecule unit-tests

## Stop service

Again, see [an example](molecule/default/converge.yaml#L52-L64) from role unit-test. This includes:

1. stop `systemd` service named `service_name`
2. get PIDs by `community.general.pids` of `process_pattern`
3. trying to `kill` processed by gathered PIDs
4. wait `murder_delay` to processes graceful exit
5. if not, `kill -9` the same PIDs list and wait again
6. if PIDs are still alive fail to the rescue
7. if any of `port_list` opened also rescue
8. rescue delay `rescue_delay` and
9. repeat 1-7 `max_rescues` times

If required checks didn't happen during numerous of rescues, the role will fail
