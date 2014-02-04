#!/usr/bin/env python

# (c) 2012, Michael DeHaan <michael.dehaan@gmail.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

########################################################

import sys

from ansible.runner import Runner
import ansible.constants as C
from ansible import utils
from ansible import errors
from ansible import callbacks
from ansible import inventory
########################################################


class Cli(object):
    ''' code behind bin/ansible '''

    # ----------------------------------------------

    def __init__(self):
        self.stats = callbacks.AggregateStats()
        self.callbacks = callbacks.CliRunnerCallbacks()

    # ----------------------------------------------

    def parse(self):
        ''' create an options parser for bin/ansible '''

        parser = utils.base_parser(
            constants=C,
            runas_opts=True,
            subset_opts=True,
            async_opts=True,
            output_opts=True,
            connect_opts=True,
            check_opts=True,
            diff_opts=False,
            usage='%prog <host-pattern> [options]'
        )

        parser.add_option('-a', '--args', dest='module_args',
            help="module arguments", default=C.DEFAULT_MODULE_ARGS)
        parser.add_option('-m', '--module-name', dest='module_name',
            help="module name to execute (default=%s)" % C.DEFAULT_MODULE_NAME,
            default=C.DEFAULT_MODULE_NAME)

        options, args = parser.parse_args()
        self.callbacks.options = options

        if len(args) == 0 or len(args) > 1:
            parser.print_help()
            sys.exit(1)

        # su and sudo command line arguments need to be mutually exclusive
        if (options.su or options.su_user or options.ask_su_pass) and \
                (options.sudo or options.sudo_user or options.ask_sudo_pass):
            parser.error("Sudo arguments ('--sudo', '--sudo-user', and '--ask-sudo-pass') "
                         "and su arguments ('-su', '--su-user', and '--ask-su-pass') are "
                         "mutually exclusive")

        return (options, args)

    # ----------------------------------------------

    def run(self, options, args):
        ''' use Runner lib to do SSH things '''

        pattern = args[0]

        inventory_manager = inventory.Inventory(options.inventory)
        if options.subset:
            inventory_manager.subset(options.subset)
        hosts = inventory_manager.list_hosts(pattern)
        if len(hosts) == 0:
            callbacks.display("No hosts matched")
            sys.exit(0)

        if options.listhosts:
            for host in hosts:
                callbacks.display('    %s' % host)
            sys.exit(0)

        if ((options.module_name == 'command' or options.module_name == 'shell')
                and not options.module_args):
            callbacks.display("No argument passed to %s module" % options.module_name, color='red', stderr=True)
            sys.exit(1)

        sshpass = None
        sudopass = None
        su_pass = None
        options.ask_pass = options.ask_pass or C.DEFAULT_ASK_PASS
        # Never ask for an SSH password when we run with local connection
        if options.connection == "local":
            options.ask_pass = False
        options.ask_sudo_pass = options.ask_sudo_pass or C.DEFAULT_ASK_SUDO_PASS
        options.ask_su_pass = options.ask_su_pass or C.DEFAULT_ASK_SU_PASS
        (sshpass, sudopass, su_pass) = utils.ask_passwords(ask_pass=options.ask_pass, ask_sudo_pass=options.ask_sudo_pass, ask_su_pass=options.ask_su_pass)
        if options.su_user or options.ask_su_pass:
            options.su = True
        elif options.sudo_user or options.ask_sudo_pass:
            options.sudo = True
        options.sudo_user = options.sudo_user or C.DEFAULT_SUDO_USER
        options.su_user = options.su_user or C.DEFAULT_SU_USER
        if options.tree:
            utils.prepare_writeable_dir(options.tree)


        runner = Runner(
            module_name=options.module_name,
            module_path=options.module_path,
            module_args=options.module_args,
            remote_user=options.remote_user,
            remote_pass=sshpass,
            inventory=inventory_manager,
            timeout=options.timeout,
            private_key_file=options.private_key_file,
            forks=options.forks,
            pattern=pattern,
            callbacks=self.callbacks,
            sudo=options.sudo,
            sudo_pass=sudopass,
            sudo_user=options.sudo_user,
            transport=options.connection,
            subset=options.subset,
            check=options.check,
            diff=options.check,
            su=options.su,
            su_pass=su_pass,
            su_user=options.su_user
        )

        if options.seconds:
            callbacks.display("background launch...\n\n", color='cyan')
            results, poller = runner.run_async(options.seconds)
            results = self.poll_while_needed(poller, options)
        else:
            results = runner.run()

        return (runner, results)

    # ----------------------------------------------

    def poll_while_needed(self, poller, options):
        ''' summarize results from Runner '''

        # BACKGROUND POLL LOGIC when -B and -P are specified
        if options.seconds and options.poll_interval > 0:
            poller.wait(options.seconds, options.poll_interval)

        return poller.results


########################################################

def main(args=sys.argv):
    #@@ fix to allow passing in arguments
    callbacks.display("", log_only=True)
    callbacks.display(" ".join(args), log_only=True)
    callbacks.display("", log_only=True)

    cli = Cli()
    (options, args) = cli.parse()
    try:
        (runner, results) = cli.run(options, args)
        for result in results['contacted'].values():
            if 'failed' in result or result.get('rc', 0) != 0:
                return 2
        if results['dark']:
            return 3
    except errors.AnsibleError, e:
        # Generic handler for ansible specific errors
        callbacks.display("ERROR: %s" % str(e), stderr=True, color='red')
        return 1
    return 0
