#!/usr/bin/env python

########################################################################
#
# (C) 2013, James Cammarata <jcammarata@ansible.com>
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
#
########################################################################

import datetime
import json
import os
import os.path
import shutil
import sys
import tarfile
import tempfile
import urllib
import urllib2
import yaml

from collections import defaultdict
from distutils.version import LooseVersion
from jinja2 import Environment
from optparse import OptionParser

import ansible.constants as C

default_meta_template = """---
galaxy_info:
  author: {{ author }}
  description: {{description}}
  company: {{ company }}
  # Some suggested licenses:
  # - BSD (default)
  # - MIT
  # - GPLv2
  # - GPLv3
  # - Apache
  # - CC-BY
  license: {{ license }}
  min_ansible_version: {{ min_ansible_version }}
  #
  # Below are all platforms currently available. Just uncomment
  # the ones that apply to your role. If you don't see your
  # platform on this list, let us know and we'll get it added!
  #
  #platforms:
  {%- for platform,versions in platforms.iteritems() %}
  #- name: {{ platform }}
  #  versions:
  #  - all
    {%- for version in versions %}
  #  - {{ version }}
    {%- endfor %}
  {%- endfor %}
  #
  # Below are all categories currently available. Just as with
  # the platforms above, uncomment those that apply to your role.
  #
  #categories:
  {%- for category in categories %}
  #- {{ category.name }}
  {%- endfor %}
dependencies: []
  # List your role dependencies here, one per line. Only
  # dependencies available via galaxy should be listed here.
  # Be sure to remove the '[]' above if you add dependencies
  # to this list.
  {% for dependency in dependencies %}
  #- {{ dependency }}
  {% endfor %}

"""

default_readme_template = """Role Name
========

A brief description of the role goes here.

Requirements
------------

Any pre-requisites that may not be covered by Ansible itself or the role should be mentioned here. For instance, if the role uses the EC2 module, it may be a good idea to mention in this section that the boto package is required.

Role Variables
--------------

A description of the settable variables for this role should go here, including any variables that are in defaults/main.yml, vars/main.yml, and any variables that can/should be set via parameters to the role. Any variables that are read from other roles and/or the global scope (ie. hostvars, group vars, etc.) should be mentioned here as well.

Dependencies
------------

A list of other roles hosted on Galaxy should go here, plus any details in regards to parameters that may need to be set for other roles, or variables that are used from other roles.

Example Playbook
-------------------------

Including an example of how to use your role (for instance, with variables passed in as parameters) is always nice for users too:

    - hosts: servers
      roles:
         - { role: username.rolename, x: 42 }

License
-------

BSD

Author Information
------------------

An optional section for the role authors to include contact information, or a website (HTML is not allowed).
"""

#-------------------------------------------------------------------------------------
# Utility functions for parsing actions/options
#-------------------------------------------------------------------------------------

VALID_ACTIONS = ("init", "info", "install", "list", "remove")

def get_action(args):
    """
    Get the action the user wants to execute from the
    sys argv list.
    """
    for i in range(0,len(args)):
        arg = args[i]
        if arg in VALID_ACTIONS:
            del args[i]
            return arg
    return None

def build_option_parser(action):
    """
    Builds an option parser object based on the action
    the user wants to execute.
    """

    usage = "usage: %%prog [%s] [--help] [options] ..." % "|".join(VALID_ACTIONS)
    epilog = "\nSee '%s <command> --help' for more information on a specific command.\n\n" % os.path.basename(sys.argv[0])
    OptionParser.format_epilog = lambda self, formatter: self.epilog
    parser = OptionParser(usage=usage, epilog=epilog)

    if not action:
        parser.print_help()
        sys.exit()

    # options for all actions
    # - none yet

    # options specific to actions
    if action == "info":
        parser.set_usage("usage: %prog info [options] role_name[,version]")
    elif action == "init":
        parser.set_usage("usage: %prog init [options] role_name")
        parser.add_option(
            '-p', '--init-path', dest='init_path', default="./",
            help='The path in which the skeleton role will be created.'
                 'The default is the current working directory.')
    elif action == "install":
        parser.set_usage("usage: %prog install [options] [-f FILE | role_name(s)[,version] | tar_file(s)]")
        parser.add_option(
            '-i', '--ignore-errors', dest='ignore_errors', action='store_true', default=False,
            help='Ignore errors and continue with the next specified role.')
        parser.add_option(
            '-n', '--no-deps', dest='no_deps', action='store_true', default=False,
            help='Don\'t download roles listed as dependencies')
        parser.add_option(
            '-r', '--role-file', dest='role_file',
            help='A file containing a list of roles to be imported')
    elif action == "remove":
        parser.set_usage("usage: %prog remove role1 role2 ...")
    elif action == "list":
        parser.set_usage("usage: %prog list [role_name]")

    # options that apply to more than one action
    if action != "init":
        parser.add_option(
            '-p', '--roles-path', dest='roles_path', default=C.DEFAULT_ROLES_PATH,
            help='The path to the directory containing your roles.'
                 'The default is the roles_path configured in your '
                 'ansible.cfg file (/etc/ansible/roles if not configured)')

    if action in ("info","init","install"):
        parser.add_option(
            '-s', '--server', dest='api_server', default="galaxy.ansible.com",
            help='The API server destination')

    if action in ("init","install"):
        parser.add_option(
            '-f', '--force', dest='force', action='store_true', default=False,
            help='Force overwriting an existing role')
    # done, return the parser
    return parser

def get_opt(options, k, defval=""):
    """
    Returns an option from an Optparse values instance.
    """
    try:
        data = getattr(options, k)
    except:
        return defval
    if k == "roles_path":
        if os.pathsep in data:
            data = data.split(os.pathsep)[0]
    return data

def exit_without_ignore(options, rc=1):
    """
    Exits with the specified return code unless the
    option --ignore-errors was specified
    """

    if not get_opt(options, "ignore_errors", False):
        print 'You can use --ignore-errors to skip failed roles.'
        sys.exit(rc)

#-------------------------------------------------------------------------------------
# Galaxy API functions
#-------------------------------------------------------------------------------------

def api_get_config(api_server):
    """
    Fetches the Galaxy API current version to ensure
    the API server is up and reachable.
    """

    try:
        url = 'https://%s/api/' % api_server
        data = json.load(urllib2.urlopen(url))
        if not data.get("current_version",None):
            return None
        else:
            return data
    except:
        return None

def api_lookup_role_by_name(api_server, role_name):
    """
    Uses the Galaxy API to do a lookup on the role owner/name.
    """

    role_name = urllib.quote(role_name)

    try:
        parts = role_name.split(".")
        user_name = ".".join(parts[0:-1])
        role_name = parts[-1]
        print " downloading role '%s', owned by %s" % (role_name, user_name)
    except:
        parser.print_help()
        print "Invalid role name (%s). You must specify username.rolename" % role_name
        sys.exit(1)

    url = 'https://%s/api/v1/roles/?owner__username=%s&name=%s' % (api_server,user_name,role_name)
    try:
        data = json.load(urllib2.urlopen(url))
        if len(data["results"]) == 0:
            return None
        else:
            return data["results"][0]
    except:
        return None

def api_fetch_role_related(api_server, related, role_id):
    """
    Uses the Galaxy API to fetch the list of related items for
    the given role. The url comes from the 'related' field of
    the role.
    """

    try:
        url = 'https://%s/api/v1/roles/%d/%s/?page_size=50' % (api_server, int(role_id), related)
        data = json.load(urllib2.urlopen(url))
        results = data['results']
        done = (data.get('next', None) == None)
        while not done:
            url = 'https://%s%s' % (api_server, data['next'])
            print url
            data = json.load(urllib2.urlopen(url))
            results += data['results']
            done = (data.get('next', None) == None)
        return results
    except:
        return None

def api_get_list(api_server, what):
    """
    Uses the Galaxy API to fetch the list of items specified.
    """

    try:
        url = 'https://%s/api/v1/%s/?page_size' % (api_server, what)
        data = json.load(urllib2.urlopen(url))
        if "results" in data:
            results = data['results']
        else:
            results = data
        done = True
        if "next" in data:
            done = (data.get('next', None) == None)
        while not done:
            url = 'https://%s%s' % (api_server, data['next'])
            print url
            data = json.load(urllib2.urlopen(url))
            results += data['results']
            done = (data.get('next', None) == None)
        return results
    except:
        print " - failed to download the %s list" % what
        return None

#-------------------------------------------------------------------------------------
# Role utility functions
#-------------------------------------------------------------------------------------

def get_role_path(role_name, options):
    """
    Returns the role path based on the roles_path option
    and the role name.
    """
    roles_path = get_opt(options,'roles_path')
    roles_path = os.path.join(roles_path, role_name)
    roles_path = os.path.expanduser(roles_path)
    return roles_path

def get_role_metadata(role_name, options):
    """
    Returns the metadata as YAML, if the file 'meta/main.yml'
    exists in the specified role_path
    """
    role_path = os.path.join(get_role_path(role_name, options), 'meta/main.yml')
    try:
        if os.path.isfile(role_path):
            f = open(role_path, 'r')
            meta_data = yaml.safe_load(f)
            f.close()
            return meta_data
        else:
            return None
    except:
        return None

def get_galaxy_install_info(role_name, options):
    """
    Returns the YAML data contained in 'meta/.galaxy_install_info',
    if it exists.
    """

    try:
        info_path = os.path.join(get_role_path(role_name, options), 'meta/.galaxy_install_info')
        if os.path.isfile(info_path):
            f = open(info_path, 'r')
            info_data = yaml.safe_load(f)
            f.close()
            return info_data
        else:
            return None
    except:
        return None

def write_galaxy_install_info(role_name, role_version, options):
    """
    Writes a YAML-formatted file to the role's meta/ directory
    (named .galaxy_install_info) which contains some information
    we can use later for commands like 'list' and 'info'.
    """

    info = dict(
        version = role_version,
        install_date = datetime.datetime.utcnow().strftime("%c"),
    )
    try:
        info_path = os.path.join(get_role_path(role_name, options), 'meta/.galaxy_install_info')
        f = open(info_path, 'w+')
        info_data = yaml.safe_dump(info, f)
        f.close()
    except:
        return False
    return True


def remove_role(role_name, options):
    """
    Removes the specified role from the roles path. There is a
    sanity check to make sure there's a meta/main.yml file at this
    path so the user doesn't blow away random directories
    """
    if get_role_metadata(role_name, options):
        role_path = get_role_path(role_name, options)
        shutil.rmtree(role_path)
        return True
    else:
        return False

def fetch_role(role_name, target, role_data, options):
    """
    Downloads the archived role from github to a temp location, extracts
    it, and then copies the extracted role to the role library path.
    """

    # first grab the file and save it to a temp location
    archive_url = 'https://github.com/%s/%s/archive/%s.tar.gz' % (role_data["github_user"], role_data["github_repo"], target)
    print " - downloading role from %s" % archive_url

    try:
        url_file = urllib2.urlopen(archive_url)
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        data = url_file.read()
        while data:
            temp_file.write(data)
            data = url_file.read()
        temp_file.close()
        return temp_file.name
    except Exception, e:
        # TODO: better urllib2 error handling for error
        #       messages that are more exact
        print "Error: failed to download the file."
        return False

def install_role(role_name, role_version, role_filename, options):
    # the file is a tar, so open it that way and extract it
    # to the specified (or default) roles directory
    if not tarfile.is_tarfile(role_filename):
        print "Error: the file downloaded was not a tar.gz"
        return False
    else:
        role_tar_file = tarfile.open(role_filename, "r:gz")
        # verify the role's meta file
        meta_file = None
        members = role_tar_file.getmembers()
        for member in members:
            if "/meta/main.yml" in member.name:
                meta_file = member
                break
        if not meta_file:
            print "Error: this role does not appear to have a meta/main.yml file."
            return False
        else:
            try:
                meta_file_data = yaml.safe_load(role_tar_file.extractfile(meta_file))
            except:
                print "Error: this role does not appear to have a valid meta/main.yml file."
                return False

        # we strip off the top-level directory for all of the files contained within
        # the tar file here, since the default is 'github_repo-target', and change it
        # to the specified role's name
        role_path = os.path.join(get_opt(options, 'roles_path', '/etc/ansible/roles'), role_name)
        role_path = os.path.expanduser(role_path)
        print " - extracting %s to %s" % (role_name, role_path)
        try:
            if os.path.exists(role_path):
                if not os.path.isdir(role_path):
                    print "Error: the specified roles path exists and is not a directory."
                    return False
                elif not get_opt(options, "force", False):
                    print "Error: the specified role %s appears to already exist. Use --force to replace it." % role_name
                    return False
                else:
                    # using --force, remove the old path
                    if not remove_role(role_name, options):
                        print "Error: %s doesn't appear to contain a role." % role_path
                        print "Please remove this directory manually if you really want to put the role here."
                        return False
            else:
                os.makedirs(role_path)

            # now we do the actual extraction to the role_path
            for member in members:
                # we only extract files
                if member.isreg():
                    member.name = "/".join(member.name.split("/")[1:])
                    role_tar_file.extract(member, role_path)

            # write out the install info file for later use
            write_galaxy_install_info(role_name, role_version, options)
        except OSError, e:
            print "Error: you do not have permission to modify files in %s" % role_path
            return False

        # return the parsed yaml metadata
        print "%s was installed successfully" % role_name
        return meta_file_data

#-------------------------------------------------------------------------------------
# Action functions
#-------------------------------------------------------------------------------------

def execute_init(args, options, parser):
    """
    Executes the init action, which creates the skeleton framework
    of a role that complies with the galaxy metadata format.
    """

    init_path  = get_opt(options, 'init_path', './')
    api_server = get_opt(options, "api_server", "galaxy.ansible.com")
    force      = get_opt(options, 'force', False)

    api_config = api_get_config(api_server)
    if not api_config:
        print "The API server (%s) is not responding, please try again later." % api_server
        sys.exit(1)

    try:
        role_name = args.pop(0).strip()
        if role_name == "":
            raise Exception("")
        role_path = os.path.join(init_path, role_name)
        if os.path.exists(role_path):
            if os.path.isfile(role_path):
                print "The path %s already exists, but is a file - aborting" % role_path
                sys.exit(1)
            elif not force:
                print "The directory %s already exists." % role_path
                print ""
                print "You can use --force to re-initialize this directory,\n" + \
                      "however it will reset any main.yml files that may have\n" + \
                      "been modified there already."
                sys.exit(1)
    except Exception, e:
        parser.print_help()
        print "No role name specified for init"
        sys.exit(1)

    ROLE_DIRS = ('defaults','files','handlers','meta','tasks','templates','vars')

    # create the default README.md
    if not os.path.exists(role_path):
        os.makedirs(role_path)
    readme_path = os.path.join(role_path, "README.md")
    f = open(readme_path, "wb")
    f.write(default_readme_template)
    f.close

    for dir in ROLE_DIRS:
        dir_path = os.path.join(init_path, role_name, dir)
        main_yml_path = os.path.join(dir_path, 'main.yml')
        # create the directory if it doesn't exist already
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)

        # now create the main.yml file for that directory
        if dir == "meta":
            # create a skeleton meta/main.yml with a valid galaxy_info
            # datastructure in place, plus with all of the available
            # tags/platforms included (but commented out) and the
            # dependencies section
            platforms = api_get_list(api_server, "platforms")
            if not platforms:
                platforms = []
            categories = api_get_list(api_server, "categories")
            if not categories:
                categories = []

            # group the list of platforms from the api based
            # on their names, with the release field being
            # appended to a list of versions
            platform_groups = defaultdict(list)
            for platform in platforms:
                platform_groups[platform['name']].append(platform['release'])
                platform_groups[platform['name']].sort()

            inject = dict(
                author = 'your name',
                company = 'your company (optional)',
                license = 'license (GPLv2, CC-BY, etc)',
                min_ansible_version = '1.2',
                platforms = platform_groups,
                categories = categories,
            )
            rendered_meta = Environment().from_string(default_meta_template).render(inject)
            f = open(main_yml_path, 'w')
            f.write(rendered_meta)
            f.close()
            pass
        elif dir not in ('files','templates'):
            # just write a (mostly) empty YAML file for main.yml
            f = open(main_yml_path, 'w')
            f.write('---\n# %s file for %s\n' % (dir,role_name))
            f.close()
    print "%s was created successfully" % role_name

def execute_info(args, options, parser):
    """
    Executes the info action. This action prints out detailed
    information about an installed role as well as info available
    from the galaxy API.
    """

    pass

def execute_install(args, options, parser):
    """
    Executes the installation action. The args list contains the
    roles to be installed, unless -f was specified. The list of roles
    can be a name (which will be downloaded via the galaxy API and github),
    or it can be a local .tar.gz file.
    """

    role_file  = get_opt(options, "role_file", None)
    api_server = get_opt(options, "api_server", "galaxy.ansible.com")
    no_deps    = get_opt(options, "no_deps", False)

    if len(args) == 0 and not role_file:
        # the user needs to specify one of either --role-file
        # or specify a single user/role name
        parser.print_help()
        print "You must specify a user/role name or a roles file"
        sys.exit()
    elif len(args) == 1 and role_file:
        # using a role file is mutually exclusive of specifying
        # the role name on the command line
        parser.print_help()
        print "Please specify a user/role name, or a roles file, but not both"
        sys.exit(1)

    api_config = api_get_config(api_server)
    if not api_config:
        print "The API server (%s) is not responding, please try again later." % api_server
        sys.exit(1)

    roles_done = []
    if role_file:
        # roles listed in a file, one per line
        # so we'll go through and grab them all
        f = open(role_file, 'r')
        roles_left = f.readlines()
        f.close()
    else:
        # roles were specified directly, so we'll just go out grab them
        # (and their dependencies, unless the user doesn't want us to).
        roles_left = args

    while len(roles_left) > 0:
        # query the galaxy API for the role data
        role_name = roles_left.pop(0).strip()
        role_version = None

        if role_name == "" or role_name.startswith("#"):
            continue
        elif role_name.find(',') != -1:
            role_name,role_version = role_name.split(',',1)
            role_name = role_name.strip()
            role_version = role_version.strip()

        if os.path.isfile(role_name):
            # installing a local tar.gz
            tar_file = role_name
            role_name = os.path.basename(role_name).replace('.tar.gz','')
            if tarfile.is_tarfile(tar_file):
                print " - installing %s as %s" % (tar_file, role_name)
                if not install_role(role_name, role_version, tar_file, options):
                    exit_without_ignore(options)
            else:
                print "%s (%s) was NOT installed successfully." % (role_name,tar_file)
                exit_without_ignore(options)
        else:
            # installing remotely
            role_data = api_lookup_role_by_name(api_server, role_name)
            if not role_data:
                print "Sorry, %s was not found on %s." % (role_name, api_server)
                continue

            role_versions = api_fetch_role_related(api_server, 'versions', role_data['id'])
            if not role_version:
                # convert the version names to LooseVersion objects
                # and sort them to get the latest version. If there
                # are no versions in the list, we'll grab the head
                # of the master branch
                if len(role_versions) > 0:
                    loose_versions = [LooseVersion(a.get('name',None)) for a in role_versions]
                    loose_versions.sort()
                    role_version = str(loose_versions[-1])
                else:
                    role_version = 'master'
                print " no version specified, installing %s" % role_version
            else:
                if role_versions and role_version not in [a.get('name',None) for a in role_versions]:
                    print "The specified version (%s) was not found in the list of available versions." % role_version
                    exit_without_ignore(options)
                    continue

            # download the role. if --no-deps was specified, we stop here,
            # otherwise we recursively grab roles and all of their deps.
            tmp_file = fetch_role(role_name, role_version, role_data, options)
            if tmp_file and install_role(role_name, role_version, tmp_file, options):
                # we're done with the temp file, clean it up
                os.unlink(tmp_file)
                # install dependencies, if we want them
                if not no_deps:
                    role_dependencies = role_data['summary_fields']['dependencies'] # api_fetch_role_related(api_server, 'dependencies', role_data['id'])
                    for dep_name in role_dependencies:
                        #dep_name = "%s.%s" % (dep['owner'], dep['name'])
                        if not get_role_metadata(dep_name, options):
                            print ' adding dependency: %s' % dep_name
                            roles_left.append(dep_name)
                        else:
                            print ' dependency %s is already installed, skipping.' % dep_name
            else:
                if tmp_file:
                    os.unlink(tmp_file)
                print "%s was NOT installed successfully." % role_name
                exit_without_ignore(options)
    sys.exit(0)

def execute_remove(args, options, parser):
    """
    Executes the remove action. The args list contains the list
    of roles to be removed. This list can contain more than one role.
    """

    if len(args) == 0:
        parser.print_help()
        print 'You must specify at least one role to remove.'
        sys.exit()

    for role in args:
        if get_role_metadata(role, options):
            if remove_role(role, options):
                print 'successfully removed %s' % role
            else:
                print "failed to remove role: %s" % role
        else:
            print '%s is not installed, skipping.' % role
    sys.exit(0)

def execute_list(args, options, parser):
    """
    Executes the list action. The args list can contain zero
    or one role. If one is specified, only that role will be
    shown, otherwise all roles in the specified directory will
    be shown.
    """

    if len(args) > 1:
        print "Please specify only one role to list, or specify no roles to see a full list"
        sys.exit(1)

    if len(args) == 1:
        # show only the request role, if it exists
        role_name = args[0]
        metadata = get_role_metadata(role_name, options)
        if metadata:
            install_info = get_galaxy_install_info(role_name, options)
            version = None
            if install_info:
                version = install_info.get("version", None)
            if not version:
                version = "(unknown version)"
            # show some more info about single roles here
            print " %s, %s" % (role_name, version)
        else:
            print "The role %s was not found" % role_name
    else:
        # show all valid roles in the roles_path directory
        roles_path = get_opt(options, 'roles_path')
        roles_path = os.path.expanduser(roles_path)
        if not os.path.exists(roles_path):
            parser.print_help()
            print "The path %s does not exist. Please specify a valid path with --roles-path" % roles_path
            sys.exit(1)
        elif not os.path.isdir(roles_path):
            print "%s exists, but it is not a directory. Please specify a valid path with --roles-path" % roles_path
            parser.print_help()
            sys.exit(1)
        path_files = os.listdir(roles_path)
        for path_file in path_files:
            if get_role_metadata(path_file, options):
                install_info = get_galaxy_install_info(path_file, options)
                version = None
                if install_info:
                    version = install_info.get("version", None)
                if not version:
                    version = "(unknown version)"
                print " %s, %s" % (path_file, version)
    sys.exit(0)

#-------------------------------------------------------------------------------------
# The main entry point
#-------------------------------------------------------------------------------------

def main(args=sys.argv):
    # parse the CLI options
    action = get_action(args)
    parser = build_option_parser(action)
    (options, args) = parser.parse_args(args[1:])

    # execute the desired action
    if 1: #try:
        fn = globals()["execute_%s" % action]
        fn(args, options, parser)
    return 0


if __name__ == "__main__":
    main()
