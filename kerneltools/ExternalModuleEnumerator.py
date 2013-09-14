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

		self._m_cchRoot = len(os.environ.get('ROOT', '/'))

		with subprocess.Popen(
			['portageq', 'vdb_path'],
			stdin = None, stdout = subprocess.PIPE
		) as procPortageQ:
			self._m_sVdbPath = str(procPortageQ.communicate()[0], encoding = 'utf-8').strip()


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

		FIRMWARE_TYPE = 0
		MODULE_TYPE = 1

		sFirmwarePath = 'lib/firmware/'
		if self._m_bPackages:
			dictFilesByPackage = dict()
			dictPackageSlots = dict()
		else:
			sFiles = ''

		with open(os.path.join(sPackagePath, 'CONTENTS'), 'r') as fileContents:
			for sLine in fileContents:
				match = re.match(
					'^(?P<type>\S+)\s+(?P<path>\S+)\s+(?P<hash>\S+)\s+(?P<size>\d+)',
					sLine
				)
				if not match or match.group('type') != 'obj':
					# Not a file.
					continue

				# Remove the root.
				sFilePath = match.group('path')[self._m_cchRoot:]
				if self._m_bModules and sFilePath.endswith('.ko'):
					iFileType = MODULE_TYPE
				elif self._m_bFirmware and sFilePath.startswith(sFirmwarePath):
					iFileType = FIRMWARE_TYPE
				else:
					# Not a file we’re interested in.
					continue

				if self._m_bPackages:
					# If the slot is not in the cache, read it now.
					if sPackagePath not in dictPackageSlots:
						with open(os.path.join(sPackagePath, 'SLOT'), 'r') as fileSlot:
							dictPackageSlots[sPackagePath] = fileSlot.read().strip()

					# Replace the package version with its slot.
					sPackage = re.sub('-[0-9].*$', ':' + dictPackageSlots[sPackagePath], sPackage)

				if self._m_bFiles:
					# If displaying file names, we want to make the path relative to the common parent
					# folder for files of that type.

					if iFileType == FIRMWARE_TYPE:
						# Remove “lib/firmware/”.
						sFilePath = sFilePath[len(sFirmwarePath):]
					elif iFileType == MODULE_TYPE:
						# Remove “lib/modules/linux-*/”.
						sFilePath = re.sub('^lib/modules/[^\/]+\/', '', sFilePath)

					if self._m_bPackages:
						# Store packages and files.
						dictFilesByPackage[sPackage] = dictFilesByPackage.get(sPackage, '') + ' ' + \
							sFilePath
					elif self._m_bFiles:
						# Store files only.
						sFiles += ' ' + sFilePath

				elif self._m_bPackages:
					# Store packages only. Since we don’t care about what’s associated to the key, just
					# store a True.
					dictFilesByPackage[sPackage] = True

		if self._m_bPackages:
			for sPackage in dictFilesByPackage:
				if self._m_bFiles:
					# Print packages and files.
					sys.stdout.write(sPackage + dictFilesByPackage[sPackage] + '\n')
				else:
					# Print packages only.
					sys.stdout.write(sPackage + '\n')
		elif self._m_bFiles and sFiles:
			# Print files only.
			# Delete the leading space.
			sFiles = sFiles[1:]
			# One file per line.
			sFiles = re.sub(' ', '\n', sFiles)
			sys.stdout.write(sFiles + '\n')



####################################################################################################
# __main__

if __name__ == '__main__':
	# TODO: test suite.
	import sys
	sys.exit(0)

