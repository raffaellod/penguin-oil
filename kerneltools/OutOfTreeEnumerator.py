#!/usr/bin/python
# -*- coding: utf-8; mode: python; tab-width: 3; indent-tabs-mode: nil -*-
#
# Copyright 2012-2013, 2015, 2017 Raffaello D. Di Napoli
#
# This file is part of kernel-tools.
#
# kernel-tools is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# kernel-tools is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along with
# kernel-tools. If not, see <http://www.gnu.org/licenses/>.
#-----------------------------------------------------------------------------

"""Implementation of the class OutOfTreeEnumerator."""

import os
import portage
import re

##############################################################################
# OutOfTreeEnumerator

class OutOfTreeEnumerator(object):
   """Enumerates kernel out-of-tree modules and firmware."""

   _contents_line_re = re.compile(r'^obj\s+(?P<path>\S+)\s+')
   _firmware_path = 'lib/firmware/'
   _module_path_prefix_re = re.compile(r'^lib/modules/[^/]+/')
   _package_version_re = re.compile(r'-[0-9].*$')

   def __init__(self, firmware, modules):
      """Constructor.

      bool firmware
         Enumerate external firmware installed by non-kernel packages.
      bool modules
         Enumerate modules installed by non-kernel packages.
      """

      root = portage.settings['EROOT']
      self._firmware = firmware
      self._modules = modules
      self._root_len = len(root)
      self._vdb_path = os.path.join(root, portage.VDB_PATH)

   def files(self):
      """Enumerates all files matching the criteria specified in the
      constructor.

      str yield
         Path to the matching file.
      """

      for package, files in self.packages_and_files(use_slot=False):
         for file_path in files:
            yield file_path

   def _get_package_kernel_modules(self, package_path):
      """Parses a package’s CONTENTS file, collecting all kernel modules
      provided by the package.

      str package_path
         Path to the package’s directory in the VDB.
      list(str) return
         List of kernel modules in the package, if any.
      """

      ret = []
      with open(os.path.join(package_path, 'CONTENTS'), 'r') as contents_file:
         for line in contents_file:
            # Parse the line.
            match = self._contents_line_re.match(line)
            if not match:
               # Not a file (“obj”).
               continue
            # Remove the root.
            file_path = match.group('path')[self._root_len:]
            if self._modules and file_path.endswith('.ko'):
               # Remove “lib/modules/linux-*/”.
               file_path = self._module_path_prefix_re.sub('', file_path)
            elif self._firmware and file_path.startswith(self._firmware_path):
               # Remove “lib/firmware/”.
               file_path = file_path[len(self._firmware_path):]
            else:
               # Not a file we’re interested in.
               continue
            # Add this file to the list.
            ret.append(file_path)
      return ret

   def _get_package_slot(self, package_path):
      """Returns the contents of a package’s SLOT file.

      str package_path
         Path to the package’s directory in the VDB.
      str return
         Package slot.
      """

      with open(os.path.join(package_path, 'SLOT'), 'r') as slot_file:
         return slot_file.read().strip()

   def packages(self, use_slot = True):
      """Enumerates all packages that installed files matching the criteria
      specified in the constructor.

      bool use_slot
         If True (default), each package will end in its slot number instead
         of its version.
      str yield
         Package.
      """

      for package, files in self.packages_and_files(use_slot):
         yield package

   def packages_and_files(self, use_slot = True):
      """Enumerates all packages and/or files matching the criteria specified
      in the constructor.

      bool use_slot
         If True (default), each package will end in its slot number instead
         of its version.
      tuple(str, list(str)) yield
         A tuple containing the package and the matching files it contains.
      """

      # List all directories (package categories) in the VDB.
      for category in os.listdir(self._vdb_path):
         category_path = os.path.join(self._vdb_path, category)
         # Ignore the sys-kernel category: kernels may contain modules, but
         # they would then be in-tree modules, not out-of-tree.
         if category == 'sys-kernel' or not os.path.isdir(category_path):
            continue
         # List all directories (package names) in the category.
         for package in os.listdir(category_path):
            package_path = os.path.join(category_path, package)
            if not os.path.isdir(package_path):
               continue
            files = self._get_package_kernel_modules(package_path)
            if files:
               package = category + '/' + package
               if use_slot:
                  # Replace the package version with its slot.
                  slot = self._get_package_slot(package_path)
                  package = self._package_version_re.sub(':' + slot, package)
               yield package, files
