#!/usr/bin/python
# -*- coding: utf-8; mode: python; tab-width: 3 -*-
#---------------------------------------------------------------------------------------------------
# kernel-tools
# Copyright 2012-2013 Raffaello D. Di Napoli
#---------------------------------------------------------------------------------------------------
# This file is part of kernel-tools.
#
# kernel-tools is free software: you can redistribute it and/or modify it under the terms of the GNU
# General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# kernel-tools is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
# even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with kernel-tools. If not,
# see <http://www.gnu.org/licenses/>.
#---------------------------------------------------------------------------------------------------

"""Implementation of the class ExternalModuleEnumerator."""

import os
import re
import subprocess
import sys



####################################################################################################
# ExternalModuleEnumerator

class ExternalModuleEnumerator(object):
	"""Enumerates kernel external modules."""

	def __init__(self, bFirmware, bModules):
		"""Constructor.

		bool bFirmware
			Enumerate external firmware installed by non-kernel packages.
		bool bModules
			Enumerate modules installed by non-kernel packages.
		"""

		self._m_reContentsLine = re.compile(r'^obj\s+(?P<path>\S+)\s+')
		self._m_bFirmware = bFirmware
		self._m_sFirmwarePath = 'lib/firmware/'
		self._m_bModules = bModules
		self._m_cchRoot = len(os.environ.get('ROOT', '/'))
		self._m_sVdbPath = subprocess.check_output(
			['portageq', 'vdb_path'],
			stderr = sys.stderr, universal_newlines = True
		).rstrip()


	def files(self):
		"""Enumerates all files matching the criteria specified in the constructor.

		str yield
			Path to the matching file.
		"""

		for sPackage, listFiles in self.packages_and_files(bUseSlot = False):
			for sFilePath in listFiles:
				yield sFilePath


	def packages(self, bUseSlot = True):
		"""Enumerates all packages that installed files matching the criteria specified in the
		constructor.

		[bool bUseSlot]
			If True (default), each package will end in its slot number instead of its version.
		str yield
			Package.
		"""

		for sPackage, listFiles in self.packages_and_files(bUseSlot = False):
			yield sPackage


	def packages_and_files(self, bUseSlot = True):
		"""Enumerates all packages and/or files matching the criteria specified in the constructor.

		[bool bUseSlot]
			If True (default), each package will end in its slot number instead of its version.
		tuple(str, list(str)) yield
			A tuple containing the package and the matching files it contains.
		"""

		# List all directories (package categories) in the VDB.
		for sCategory in os.listdir(self._m_sVdbPath):
			sCategoryPath = os.path.join(self._m_sVdbPath, sCategory)
			if not os.path.isdir(sCategoryPath):
				continue
			# List all directories (package names) in the category.
			for sPackage in os.listdir(sCategoryPath):
				sPackagePath = os.path.join(sCategoryPath, sPackage)
				if not os.path.isdir(sPackagePath):
					continue
				sPackage = sCategory + '/' + sPackage

				# Analyze the contents of the package, building a list of files of our interest.
				listFiles = []
				with open(os.path.join(sPackagePath, 'CONTENTS'), 'r') as fileContents:
					for sLine in fileContents:
						# Parse the line.
						match = self._m_reContentsLine.match(sLine)
						if not match:
							# Not a file (“obj”).
							continue
						# Remove the root.
						sFilePath = match.group('path')[self._m_cchRoot:]
						if self._m_bModules and sFilePath.endswith('.ko'):
							# Remove “lib/modules/linux-*/”.
							sFilePath = re.sub(r'^lib/modules/[^/]+/', '', sFilePath)
						elif self._m_bFirmware and sFilePath.startswith(self._m_sFirmwarePath):
							# Remove “lib/firmware/”.
							sFilePath = sFilePath[len(self._m_sFirmwarePath):]
						else:
							# Not a file we’re interested in.
							continue
						# Add this file to the list.
						listFiles.append(sFilePath)

				if listFiles:
					if bUseSlot:
						# Replace the package version with its slot.
						with open(os.path.join(sPackagePath, 'SLOT'), 'r') as fileSlot:
							sPackageSlot = fileSlot.read().strip()
						sPackage = re.sub(r'-[0-9].*$', ':' + sPackageSlot, sPackage)
					yield sPackage, listFiles



####################################################################################################
# __main__

if __name__ == '__main__':
	# TODO: test suite.
	import sys
	sys.exit(0)

