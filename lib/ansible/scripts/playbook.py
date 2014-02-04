import sys
import os
import stat

import ansible.playbook
import ansible.constants as C
import ansible.utils.template
from ansible import errors
from ansible import callbacks
from ansible import utils
from ansible.color import ANSIBLE_COLOR, stringc
from ansible.callbacks import display


def colorize(lead, num, color):
    """ Print 'lead' = 'num' in 'color' """
    if num != 0 and ANSIBLE_COLOR and color is not None:
        return "%s%s%-15s" % (stringc(lead, color), stringc("=", color), stringc(str(num), color))
    else:
        return "%s=%-4s" % (lead, str(num))


def hostcolor(host, stats, color=True):
    if ANSIBLE_COLOR and color:
        if stats['failures'] != 0 or stats['unreachable'] != 0:
            return "%-37s" % stringc(host, 'red')
        elif stats['changed'] != 0:
            return "%-37s" % stringc(host, 'yellow')
        else:
            return "%-37s" % stringc(host, 'green')
    return "%-26s" % host


def run_playbook(args):
    ''' run ansible-playbook operations '''

    # create parser for CLI options
    parser = utils.base_parser(
        constants=C,
        usage = "%prog playbook.yml",
        connect_opts=True,
        runas_opts=True,
        subset_opts=True,
        check_opts=True,
        diff_opts=True
    )
    parser.add_option('-e', '--extra-vars', dest="extra_vars", action="append",
        help="set additional variables as key=value or YAML/JSON", default=[])
    parser.add_option('-t', '--tags', dest='tags', default='all',
        help="only run plays and tasks tagged with these values")
    parser.add_option('--skip-tags', dest='skip_tags',
        help="only run plays and tasks whose tags do not match these values")
    parser.add_option('--syntax-check', dest='syntax', action='store_true',
        help="perform a syntax check on the playbook, but do not execute it")
    parser.add_option('--list-tasks', dest='listtasks', action='store_true',
        help="list all tasks that would be executed")
    parser.add_option('--step', dest='step', action='store_true',
        help="one-step-at-a-time: confirm each task before running")
    parser.add_option('--start-at-task', dest='start_at',
        help="start the playbook at the task matching this name")

    options, args = parser.parse_args(args)

    if len(args) == 0:
        parser.print_help(file=sys.stderr)
        return 1

    # su and sudo command line arguments need to be mutually exclusive
    if (options.su or options.su_user or options.ask_su_pass) and \
                (options.sudo or options.sudo_user or options.ask_sudo_pass):
            parser.error("Sudo arguments ('--sudo', '--sudo-user', and '--ask-sudo-pass') "
                         "and su arguments ('-su', '--su-user', and '--ask-su-pass') are "
                         "mutually exclusive")

    inventory = ansible.inventory.Inventory(options.inventory)
    inventory.subset(options.subset)
    if len(inventory.list_hosts()) == 0:
        raise errors.AnsibleError("provided hosts list is empty")

    sshpass = None
    sudopass = None
    su_pass = None
    if not options.listhosts and not options.syntax and not options.listtasks:
        options.ask_pass = options.ask_pass or C.DEFAULT_ASK_PASS
        # Never ask for an SSH password when we run with local connection
        if options.connection == "local":
            options.ask_pass = False
        options.ask_sudo_pass = options.ask_sudo_pass or C.DEFAULT_ASK_SUDO_PASS
        options.ask_su_pass = options.ask_su_pass or C.DEFAULT_ASK_SU_PASS
        (sshpass, sudopass, su_pass) = utils.ask_passwords(ask_pass=options.ask_pass, ask_sudo_pass=options.ask_sudo_pass, ask_su_pass=options.ask_su_pass)
        options.sudo_user = options.sudo_user or C.DEFAULT_SUDO_USER
        options.su_user = options.su_user or C.DEFAULT_SU_USER


    extra_vars = {}
    for extra_vars_opt in options.extra_vars:
        if extra_vars_opt.startswith("@"):
            # Argument is a YAML file (JSON is a subset of YAML)
            extra_vars = utils.combine_vars(extra_vars, utils.parse_yaml_from_file(extra_vars_opt[1:]))
        elif extra_vars_opt and extra_vars_opt[0] in '[{':
            # Arguments as YAML
            extra_vars = utils.combine_vars(extra_vars, utils.parse_yaml(extra_vars_opt))
        else:
            # Arguments as Key-value
            extra_vars = utils.combine_vars(extra_vars, utils.parse_kv(extra_vars_opt))

    only_tags = options.tags.split(",")
    skip_tags = options.skip_tags
    if options.skip_tags is not None:
        skip_tags = options.skip_tags.split(",")

    for playbook in args:
        if not os.path.exists(playbook):
            raise errors.AnsibleError("the playbook: %s could not be found" % playbook)
        if not (os.path.isfile(playbook) or stat.S_ISFIFO(os.stat(playbook).st_mode)):
            raise errors.AnsibleError("the playbook: %s does not appear to be a file" % playbook)

    # run all playbooks specified on the command line
    for playbook in args:

        # let inventory know which playbooks are using so it can know the basedirs
        inventory.set_playbook_basedir(os.path.dirname(playbook))

        stats = callbacks.AggregateStats()
        playbook_cb = callbacks.PlaybookCallbacks(verbose=utils.VERBOSITY)
        if options.step:
            playbook_cb.step = options.step
        if options.start_at:
            playbook_cb.start_at = options.start_at
        runner_cb = callbacks.PlaybookRunnerCallbacks(stats, verbose=utils.VERBOSITY)

        pb = ansible.playbook.PlayBook(
            playbook=playbook,
            module_path=options.module_path,
            inventory=inventory,
            forks=options.forks,
            remote_user=options.remote_user,
            remote_pass=sshpass,
            callbacks=playbook_cb,
            runner_callbacks=runner_cb,
            stats=stats,
            timeout=options.timeout,
            transport=options.connection,
            sudo=options.sudo,
            sudo_user=options.sudo_user,
            sudo_pass=sudopass,
            extra_vars=extra_vars,
            private_key_file=options.private_key_file,
            only_tags=only_tags,
            skip_tags=skip_tags,
            check=options.check,
            diff=options.diff,
            su=options.su,
            su_pass=su_pass,
            su_user=options.su_user
        )

        if options.listhosts or options.listtasks or options.syntax:
            print ''
            print 'playbook: %s' % playbook
            print ''
            playnum = 0
            for (play_ds, play_basedir) in zip(pb.playbook, pb.play_basedirs):
                playnum += 1
                play = ansible.playbook.Play(pb, play_ds, play_basedir)
                label = play.name
                if options.listhosts:
                    hosts = pb.inventory.list_hosts(play.hosts)
                    print '  play #%d (%s): host count=%d' % (playnum, label, len(hosts))
                    for host in hosts:
                        print '    %s' % host
                if options.listtasks:
                    matched_tags, unmatched_tags = play.compare_tags(pb.only_tags)

                    # Remove skipped tasks
                    matched_tags = matched_tags - set(pb.skip_tags)

                    unmatched_tags.discard('all')
                    unknown_tags = ((set(pb.only_tags) | set(pb.skip_tags)) -
                                    (matched_tags | unmatched_tags))

                    if unknown_tags:
                        continue
                    print '  play #%d (%s):' % (playnum, label)

                    for task in play.tasks():
                        if (set(task.tags).intersection(pb.only_tags) and not
                            set(task.tags).intersection(pb.skip_tags)):
                            if getattr(task, 'name', None) is not None:
                                # meta tasks have no names
                                print '    %s' % task.name
                print ''
            continue

        if options.syntax:
            # if we've not exited by now then we are fine.
            print 'Playbook Syntax is fine'
            return 0

        failed_hosts = []
        unreachable_hosts = []

        try:

            pb.run()

            hosts = sorted(pb.stats.processed.keys())
            display(callbacks.banner("PLAY RECAP"))
            playbook_cb.on_stats(pb.stats)

            for h in hosts:
                t = pb.stats.summarize(h)
                if t['failures'] > 0:
                    failed_hosts.append(h)
                if t['unreachable'] > 0:
                    unreachable_hosts.append(h)

            retries = failed_hosts + unreachable_hosts

            if len(retries) > 0:
                filename = pb.generate_retry_inventory(retries)
                if filename:
                    display("           to retry, use: --limit @%s\n" % filename)

            for h in hosts:
                t = pb.stats.summarize(h)

                display("%s : %s %s %s %s" % (
                    hostcolor(h, t),
                    colorize('ok', t['ok'], 'green'),
                    colorize('changed', t['changed'], 'yellow'),
                    colorize('unreachable', t['unreachable'], 'red'),
                    colorize('failed', t['failures'], 'red')),
                    screen_only=True
                )

                display("%s : %s %s %s %s" % (
                    hostcolor(h, t, False),
                    colorize('ok', t['ok'], None),
                    colorize('changed', t['changed'], None),
                    colorize('unreachable', t['unreachable'], None),
                    colorize('failed', t['failures'], None)),
                    log_only=True
                )

            print ""
            if len(failed_hosts) > 0:
                return 2
            if len(unreachable_hosts) > 0:
                return 3

        except errors.AnsibleError, e:
            display("ERROR: %s" % e, color='red')
            return 1

    return 0


def main(args=sys.argv):
    display(" ", log_only=True)
    display(" ".join(args), log_only=True)
    display(" ", log_only=True)
    try:
        return run_playbook(args[1:])
    except errors.AnsibleError, e:
        display("ERROR: %s" % e, color='red', stderr=True)
        return 1
    except KeyboardInterrupt:
        display("ERROR: interrupted", color='red', stderr=True)
        return 1

    
if __name__ == "__main__":
    main()
