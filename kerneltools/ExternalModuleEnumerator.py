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

	def __init__(self, bFiles, bFirmware, bModules, bPackages):
		"""Constructor.

		bool bFiles
			Show matching files for each enumerated item.
		bool bFirmware
			Enumerate external firmware installed by non-kernel packages.
		bool bModules
			Enumerate modules installed by non-kernel packages.
		bool bPackages
			If bFiles is False, only show package names; if bFiles is True, show one matching package
			per line, with its relevant contents following the package name, on the same line
		"""

		self._m_bFiles = bFiles
		self._m_bFirmware = bFirmware
		self._m_bModules = bModules
		self._m_bPackages = bPackages

		self._m_reContentsLine = re.compile('^obj\s+(?P<path>\S+)\s+')
		self._m_cchRoot = len(os.environ.get('ROOT', '/'))
		self._m_sFirmwarePath = 'lib/firmware/'

		with subprocess.Popen(
			['portageq', 'vdb_path'],
			stdout = subprocess.PIPE, stderr = sys.stderr, universal_newlines = True
		) as procPortageQ:
			self._m_sVdbPath = procPortageQ.communicate()[0].rstrip()


	def enum(self):
		"""Enumerates the packages and/or files matching the criteria specified in the constructor."""

		# List all directories (package categories) in the VDB.
		for sCategory in os.listdir(self._m_sVdbPath):
			sCategoryPath = os.path.join(self._m_sVdbPath, sCategory)
			if os.path.isdir(sCategoryPath):
				# List all directories (package names) in the category.
				for sPackageName in os.listdir(sCategoryPath):
					sPackagePath = os.path.join(sCategoryPath, sPackageName)
					if os.path.isdir(sPackagePath):
						# Process this package.
						self.process_package(sCategory + '/' + sPackageName, sPackagePath)


	def process_package(self, sPackage, sPackagePath):
		"""Examines the contents of a package, looking for files matching the criteria specified in
		the constructor.

		str sPackage
			Package category/name.
		str sPackagePath
			Path to the package in the VDB.
		"""

		# Analyze the contents of the package, building a list of files of our interest.
		listFiles = list()
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
					sFilePath = re.sub('^lib/modules/[^/]+/', '', sFilePath)
				elif self._m_bFirmware and sFilePath.startswith(self._m_sFirmwarePath):
					# Remove “lib/firmware/”.
					sFilePath = sFilePath[len(self._m_sFirmwarePath):]
				else:
					# Not a file we’re interested in.
					continue
				# Add this file to the list.
				listFiles.append(sFilePath)

		if listFiles:
			# Replace the package version with its slot.
			if self._m_bPackages:
				# Get the package slot.
				with open(os.path.join(sPackagePath, 'SLOT'), 'r') as fileSlot:
					sPackageSlot = fileSlot.read().strip()
				sPackage = re.sub('-[0-9].*$', ':' + sPackageSlot, sPackage)
			if self._m_bPackages:
				if self._m_bFiles:
					# Output the package and its files.
					sys.stdout.write(sPackage + ' ' + ' '.join(listFiles) + '\n')
				else:
					# Output packages only.
					sys.stdout.write(sPackage + '\n')
			elif self._m_bFiles:
				# Output files only, one per line.
				sys.stdout.write('\n'.join(listFiles) + '\n')



####################################################################################################
# __main__

if __name__ == '__main__':
	# TODO: test suite.
	import sys
	sys.exit(0)

