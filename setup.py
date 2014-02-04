#!/usr/bin/env python

import os
import sys
from glob import glob

from setuptools import setup
from setuptools import find_packages

packages = find_packages('lib')

sys.path.insert(0, os.path.abspath('lib'))
from ansible import __version__, __author__

# find library modules
from ansible.constants import DEFAULT_MODULE_PATH
dirs=os.listdir("./library/")
data_files = []
for i in dirs:
    data_files.append((os.path.join(DEFAULT_MODULE_PATH, i), glob('./library/' + i + '/*')))


setup(name='ansible',
      version=__version__,
      description='Radically simple IT automation',
      author=__author__,
      author_email='michael@ansible.com',
      url='http://ansible.com/',
      license='GPLv3',
      install_requires=['paramiko', 'jinja2', "PyYAML"],
      package_dir={ '': 'lib' },
      packages=packages,
      entry_points = """
      [console_scripts]
      ansible = ansible.scripts._ansible:main
      ansible-playbook = ansible.scripts.playbook:main
      ansible-pull = ansible.scripts.pull:main
      ansible-doc = ansible.scripts.doc:main
      ansible-galaxy = ansible.scripts.galaxy:main
      """,
      data_files=data_files
)
