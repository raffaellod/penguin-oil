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

"""Implementation of the class Generator."""

import glob
import os
import re
import shlex
import shutil
import subprocess
import sys
from . import ExternalModuleEnumerator



####################################################################################################
# Generator

class Generator(object):
	"""Generates a kernel binary+modules and/or tarball, optionally generating an initramfs from a
	compatible self-contained initramfs-building system (such as tinytium).
	"""

	def __init__(
		self, sPArch, sIrfSourcePath, bIrfDebug, bRebuildModules, sRoot, sSourcePath
	):
		"""Constructor. TODO: comment"""

		self._m_fileNullOut = open(os.devnull, 'w')
		self._m_listKMakeArgs = ['make', '-C', sSourcePath]

		with subprocess.Popen(
			['portageq', 'envvar', 'ARCH', 'MAKEOPTS', 'ROOT', 'PORTAGE_TMPDIR', 'FEATURES'],
			stdout = subprocess.PIPE, universal_newlines = True
		) as procPortageQ:
			sPDefArch = procPortageQ.stdout.readline().rstrip()
			self._m_listKMakeArgs.extend(shlex.split(procPortageQ.stdout.readline().rstrip()))
			self._m_PRoot = procPortageQ.stdout.readline().rstrip()
			self._m_PTmpDir = procPortageQ.stdout.readline().rstrip()
			sFeatures = procPortageQ.stdout.readline()
			# Eat any remaining output, then wait for termination.
			procPortageQ.communicate()

		self._m_sIndent = ''
		self._m_sIrfComprExt = ''
		self._m_bIrfDebug = bIrfDebug
		self._m_sIrfSourcePath = sIrfSourcePath
		if sPArch == None:
			self._m_sPArch = sPDefArch
		else:
			self._m_sPArch = sPArch
		self._m_bRebuildModules = bRebuildModules
		if sRoot == None:
			self._m_sRoot = self._m_PRoot
		else:
			self._m_sRoot = sRoot
		self._m_sSourcePath = sSourcePath
		self._m_sSrcConfigPath = None
		self._m_sSrcImagePath = None
		self._m_sSrcIrfArchivePath = None
		self._m_sSrcSysmapPath = None
		self._m_bUseDistCC = re.search(r'\bdistcc\b', sFeatures) != None


	def __del__(self):
		"""Destructor."""

		if self._m_sSrcIrfArchivePath:
			self.einfo('Cleaning up temporary files ...\n')
			try:
				os.unlink(self._m_sSrcIrfArchivePath)
			except OSError:
				# Maybe the file name was initialized, but the file itself hadn’t yet been created.
				pass
		self._m_fileNullOut.close()


	def eindent(self):
		"""TODO: comment"""

		self._m_sIndent += '  '


	def eoutdent(self):
		"""TODO: comment"""

		self._m_sIndent = self._m_sIndent[:-2]


	def einfo(self, s):
		"""TODO: comment"""

		sys.stdout.write(self._m_sIndent + '[I] ' + s)


	def ewarn(self, s):
		"""TODO: comment"""

		sys.stdout.write(self._m_sIndent + '[W] ' + s)


	def eerror(self, s):
		"""TODO: comment"""

		sys.stdout.write(self._m_sIndent + '[E] ' + s)
		raise Exception(s)


	def einfo_sizediff(self, sSubject, iPrevSize, iNewSize):
		"""TODO: comment"""

		if iPrevSize == iNewSize:
			self.einfo('{} size unchanged at {} KiB\n'.format(sSubject, int((iNewSize + 1023) / 1024)))
		elif iPrevSize == 0:
			self.einfo('{} size is {} KiB\n'.format(sSubject, int((iNewSize + 1023) / 1024)))
		else:
			if iNewSize > iPrevSize:
				sPlusSign = '+'
			else:
				# A minus will be printed as part of the number.
				sPlusSign = ''
			self.einfo('{} size changed from {} KiB to {} KiB ({}{}%)\n'.format(
				sSubject,
				int((iPrevSize + 1023) / 1024),
				int((iNewSize + 1023) / 1024),
				sPlusSign,
				int((iNewSize - iPrevSize) * 100 / iPrevSize)
			))


	def get_kernel_version(self):
		"""Retrieves the kernel version for the source directory specified in the constructor.

		str return
			Kernel version reported by “make kernelrelease”. Also available as self._m_sKernelVersion.
		"""

		self._m_sKernelVersion = subprocess.check_output(
			self._m_listKMakeArgs + ['-s', 'kernelrelease'],
			universal_newlines = True
		).rstrip()
		return self._m_sKernelVersion


	def build_dst_paths(self, sRoot):
		"""Calculates the destination paths for each file to be installed/packaged.

		str sRoot
			Absolute path to which the calculated paths will be relative.
		"""

		self._m_sDstImagePath = os.path.join(sRoot, 'boot/linux-' + self._m_sKernelVersion)
		self._m_sDstIrfArchivePath = os.path.join(sRoot, 'boot/initramfs-{}.cpio{}'.format(
			self._m_sKernelVersion, self._m_sIrfComprExt
		))
		self._m_sDstConfigPath = os.path.join(sRoot, 'boot/config-' + self._m_sKernelVersion)
		self._m_sDstSysmapPath = os.path.join(sRoot, 'boot/System.map-' + self._m_sKernelVersion)
		self._m_sDstModulesDir = os.path.join(sRoot, 'lib/modules/' + self._m_sKernelVersion)


	@staticmethod
	def modules_size(sDir):
		"""Calculates the size in bytes of the kernel modules contained in the specified directory.

		str sDir
			Directory containing kernel modules.
		int return
			Total size of the kernel modules in sDir.
		"""

		cbModules = 0
		# TODO: replace child process with Python code.
		with subprocess.Popen(
			['find', sDir, '-name', '*.ko', '-printf', '%s\\n'],
			stdout = subprocess.PIPE, universal_newlines = True
		) as procFind:
			for sLine in procFind.stdout:
				cbModules += int(sLine.rstrip(), 10)
		return cbModules


	def load_kernel_config(self, sConfigPath, sKernelVersion):
		"""Loads the specified kernel configuration file (.config), returning the entries defined in
		it and verifying that it’s for a specific kernel version.

		str sConfigPath
			Configuration file.
		str sKernelVersion
			Kernel version to be matched.
		dict(object) return
			Configuration entries.
		"""

		dictKernelConfig = {}
		with open(sConfigPath, 'r') as fileConfig:
			iLine = 1
			bConfigVersionFound = False
			for sLine in fileConfig:
				if not bConfigVersionFound:
					# In the first 5 lines, expect to find a line like these, indicating that that the
					# kernel has already been configured:
					#    Linux/i386 2.6.37 Kernel Configuration
					#    Linux kernel version: 2.6.34
					if iLine < 5:
						iLine += 1
						match = re.match(r'^# Linux/\S* (?P<version>\S*) Kernel Configuration', sLine)
						if not match:
							match = re.match(r'^# Linux kernel version: (?P<version>\S+)', sLine)
						if match:
							bConfigVersionFound = match.group('version') == sKernelVersion
							continue
					else:
						self.eerror('This kernel needs to be configured first.\n')
						self.eerror('Try:\n')
						self.eerror("	make -C '{}' menuconfig\n".format(self._m_sSourcePath))
				else:
					match = re.match(r'^(?P<name>CONFIG_\S+)+=(?P<value>.*)$', sLine)
					if match:
						sValue = match.group('value')
						if sValue == 'y':
							oValue = True
						elif sValue == 'n':
							oValue = False
						elif len(sValue) >= 2 and sValue[0] == '"' and sValue[:-1] == '"':
							oValue = sValue[1:-1]
						else:
							oValue = sValue
						dictKernelConfig[match.group('name')] = oValue
		return dictKernelConfig


	def execute(self):
		"""TODO: comment"""

		# Determine the ARCH and the generated kernel file name.
		sKArch = self._m_sPArch
		sSrcImageRelPath = None
		if self._m_sPArch == 'x86':
			sKArch = 'i386'
			sSrcImageRelPath = 'arch/x86/boot/bzImage'
		elif self._m_sPArch == 'amd64':
			sKArch = 'x86_64'
			sSrcImageRelPath = 'arch/x86/boot/bzImage'
		elif self._m_sPArch == 'ppc':
			sKArch = 'powerpc'
		else:
			self.eerror('Unsupported ARCH: {}\n'.format(self._m_sPArch))
		os.environ['ARCH'] = sKArch
		os.environ['PORTAGE_ARCH'] = self._m_sPArch

		# Ensure we have a valid kernel, and get its version.
		if not self.get_kernel_version():
			# No kernel was specified: find one, first checking if the standard symlink is in place.
			self._m_sSourcePath = os.path.join(self._m_PRoot, 'usr/src/linux')
			if not os.path.isdir(self._m_sSourcePath):
				self.eerror(
					'No suitable kernel source directory was found; please consider using the\n'
				)
				self.eerror(
					'--source option, or invoke kernel-gen from within a kernel source directory.\n'
				)
				self.eerror('\n')
				self.eerror(
					'You can enable the [1;34msymlink[0m USE flag to keep an up-to-date ' +
					'symlink to your\n'
				)
				self.eerror('current kernel source directory in [1;36m/usr/src/linux[0m.\n')
				self.eerror('\n')
				self.eerror('Unable to locate a kernel source directory.\n')
			if not self.get_kernel_version():
				self.eerror('Unable to determine the version of the selected kernel source.\n')

		self._m_sSourcePath = os.path.abspath(self._m_sSourcePath)
		self._m_sSrcConfigPath = os.path.join(self._m_sSourcePath, '.config')
		self._m_sSrcSysmapPath = os.path.join(self._m_sSourcePath, 'System.map')
		os.environ['KERNEL_DIR'] = self._m_sSourcePath
		os.environ['ROOT'] = self._m_sRoot

		dictKernelConfig = self.load_kernel_config(self._m_sSrcConfigPath, self._m_sKernelVersion)

		# Get the kernel image compression method from the config file.
		sKernelCompressor = None
		for sCompressor in 'LZO', 'LZMA', 'BZIP2', 'GZIP':
			if dictKernelConfig.get('CONFIG_KERNEL_' + sCompressor):
				sKernelCompressor = sCompressor
				break

		# If default, or if not compressed, just use the plain image.
		if not sSrcImageRelPath or not sKernelCompressor:
			sSrcImageRelPath = 'vmlinux'
		self._m_sSrcImagePath = os.path.join(self._m_sSourcePath, sSrcImageRelPath)
		del sSrcImageRelPath

		if self._m_sIrfSourcePath:
			# Check for initramfs/initrd support with the config file.
			if dictKernelConfig.get('CONFIG_BLK_DEV_INITRD'):
				if self._m_sIrfSourcePath == True:
					self._m_sIrfSourcePath = os.path.join(self._m_PRoot, 'usr/src/initramfs')
				if not os.path.isdir(self._m_sIrfSourcePath):
					self.ewarn('The selected kernel was configured to support initramfs/initrd,\n')
					self.ewarn('but no suitable initramfs source directory was specified or found.\n')
					self.ewarn('No initramfs will be created.\n')
					self._m_sIrfSourcePath = None
			else:
				if self._m_sIrfSourcePath:
					self.eerror('\n')
					self.eerror('The selected kernel was not configured to support initramfs/initrd.\n')

		if self._m_sIrfSourcePath:
			# TODO: check that these CONFIG_ match:
			#   +DEVTMPFS

			# Check for an enabled initramfs compression method.
			sIrfCompressor = None
			listEnabledIrfCompressors = []
			for sCompressor in 'LZO', 'LZMA', 'BZIP2', 'GZIP':
				if dictKernelConfig.get('CONFIG_RD_' + sCompressor):
					if sCompressor == sKernelCompressor:
						# We can pick the same compression for kernel image and initramfs.
						sIrfCompressor = sKernelCompressor
						break
					# Not the same as the kernel image, but make a note of this in case the condition
					# above is never satisfied.
					listEnabledIrfCompressors.append(sCompressor)
			# If this is still None, pick the first enabled compression method, if any.
			if not sIrfCompressor and listEnabledIrfCompressors:
				sIrfCompressor = listEnabledIrfCompressors[0]
			if sIrfCompressor:
				# Pick the corresponding filename extension.
				self._m_sIrfComprExt = {
					'BZIP2': '.bz2',
					'GZIP' : '.gz',
					'LZMA' : '.lzma',
					'LZO'  : '.lzo',
				}[sIrfCompressor]
				# Pick the corresponding compressor executable.
				sIrfCompressor = {
					'BZIP2': 'bzip2',
					'GZIP' : 'gzip',
					'LZMA' : 'lzma',
					'LZO'  : 'lzop',
				}[sIrfCompressor]
			self._m_sSrcIrfArchivePath = os.path.join(
				self._m_PTmpDir, 'initramfs.cpio' + self._m_sIrfComprExt
			)

		# Determine if cross-compiling.
		sCrossCompiler = dictKernelConfig.get('CONFIG_CROSS_COMPILE')
		os.environ['CROSS_COMPILE'] = sCrossCompiler


		self.einfo('Preparing to build:\n')
		self.eindent()
		self.einfo('[1;32mlinux-{}[0m ({})\n'.format(self._m_sKernelVersion, sKArch))
		self.einfo('from [1;37m{}[0m\n'.format(self._m_sSourcePath))

		if self._m_sIrfSourcePath:
			# Check that a valid initramfs directory was specified.
			self._m_sIrfSourcePath = os.path.realpath(self._m_sIrfSourcePath)
			self.einfo('with initramfs from [1;37m{}[0m\n'.format(self._m_sIrfSourcePath))
		if sCrossCompiler:
			self.einfo('cross-compiled with [1;37m{}[0m toolchain\n'.format(sCrossCompiler))
		self.eoutdent()
		self.einfo('\n')

		# Use distcc, if enabled.
		# TODO: also add HOSTCC.
		if self._m_bUseDistCC:
			self.einfo('Distributed C compiler (distcc) enabled')
			self._m_listKMakeArgs.append('CC=distcc')
			sDistCCDir = os.path.join(self._m_PTmpDir, 'portage/.distcc')
			os.makedirs(sDistCCDir, 0o755, exist_ok = True)
			os.environ['DISTCC_DIR'] = sDistCCDir
			del sDistCCDir


		# Only invoke make if .config was changed since last compilation.
		if not os.path.exists(self._m_sSrcImagePath) or \
			os.path.getmtime(self._m_sSrcConfigPath) > os.path.getmtime(self._m_sSrcImagePath) \
		:
			self.einfo('Building linux-{} ...\n'.format(self._m_sKernelVersion))
			subprocess.check_call(
				self._m_listKMakeArgs, # "${@}"
				stdout = self._m_fileNullOut
			)
			self.einfo('Finished building linux-{}\n'.format(self._m_sKernelVersion))

			# kmake won’t touch the kernel image if .config doesn’t require so, which means that the
			# above test would always cause this if branch to be entered. A way to avoid this is to
			# touch the kernel image now.
			os.utime(self._m_sSrcImagePath, None)

			if self._m_bRebuildModules:
				self.einfo('Rebuilding kernel module packages ...\n')
				eme = ExternalModuleEnumerator(bFirmware = False, bModules = True)
				listModulePackages = list(eme.packages())
				if listModulePackages:
					subprocess.check_call(
						[sCrossCompiler + 'emerge', '-q1', '--usepkg=n', '--quiet-build'] +
							listModulePackages,
						stdout = self._m_fileNullOut
					)

		if self._m_sIrfSourcePath:
			sPrevDir = os.getcwd()
			sIrfWorkDir = os.path.join(self._m_PTmpDir, 'initramfs-' + self._m_sKernelVersion)
			shutil.rmtree(sIrfWorkDir, ignore_errors = True)
			os.mkdir(sIrfWorkDir, 0o755)
			try:
				os.chdir(sIrfWorkDir)

				self.einfo('Generating initramfs\n')
				self.eindent()

				self.einfo('Adding kernel modules ...\n')
				subprocess.check_call(
					self._m_listKMakeArgs + ['INSTALL_MOD_PATH=' + sIrfWorkDir, 'modules_install'],
					stdout = self._m_fileNullOut
				)
				# TODO: more proper way of excluding modules from the initramfs.
#				rm -rf sIrfWorkDir/lib*/modules/*/kernel/sound

				self.einfo('Adding out-of-tree firmware ...\n')
				# Create the folder beforehand; it not needed, we'll delete it later.
				sSrcFirmwareDir = os.path.join(self._m_sRoot, 'lib/firmware')
				sDstFirmwareDir = os.path.join(sIrfWorkDir, 'lib/firmware')
				eme = ExternalModuleEnumerator(bFirmware = True, bModules = False)
				for sSrcExtFirmwarePath in eme.files():
					sDstExtFirmwarePath = os.path.join(sDstFirmwareDir, sSrcExtFirmwarePath)
					os.makedirs(os.path.dirname(sDstExtFirmwarePath), 0o755, exist_ok = True)
					# Copy the firmware file.
					shutil.copy2(
						os.path.join(sSrcFirmwareDir, sSrcExtFirmwarePath),
						sDstExtFirmwarePath
					)

				sIrfBuild = os.path.join(self._m_sIrfSourcePath, 'build')
				if os.path.isfile(sIrfBuild) and os.access(sIrfBuild, os.R_OK | os.X_OK):
					# The initramfs has a build script; invoke it.
					self.einfo('Invoking initramfs custom build script\n')
					self.eindent()
					# ARCH, PORTAGE_ARCH and CROSS_COMPILE are already set in os.environ.
					subprocess.check_call([sIrfBuild])
					self.eoutdent()
				else:
					# No build script; just copy every file.
					self.einfo('Adding source files ...\n')
					for sIrfFile in os.listdir(self._m_sIrfSourcePath):
						shutil.copytree(os.path.join(self._m_sIrfSourcePath, sIrfFile), sIrfWorkDir)

				if self._m_bIrfDebug:
					sIrfDumpFileName = os.path.join(
						self._m_PTmpDir, 'initramfs-' + self._m_sKernelVersion + '.ls'
					)
					with open(sIrfDumpFileName, 'w') as fileIrfDump:
						self.einfo('Dumping contents of generated initramfs to {} ...\n'.format(
							sIrfDumpFileName
						))
						subprocess.check_call(
							['ls', '-lR', '--color=always'],
							stdout = fileIrfDump, universal_newlines = True
						)
					del sIrfDumpFileName

				self.einfo('Creating archive ...\n')
				with subprocess.Popen(
					['find', '.', '-mindepth', '1', '-printf', '%P\n'],
					stdout = subprocess.PIPE
				) as procFind:
					with open(self._m_sSrcIrfArchivePath, 'wb') as fileIrfArchive:
						if sIrfCompressor:
							fileCpioStdout = subprocess.PIPE
						else:
							fileCpioStdout = fileIrfArchive
						# Redirect cpio’s output to /dev/null, since it likes to output junk.
						with subprocess.Popen(
							['cpio', '--create', '--format', 'newc'],
							stdin = procFind.stdout, stdout = fileCpioStdout, stderr = self._m_fileNullOut
						) as procCpio:
							if sIrfCompressor:
								with subprocess.Popen(
									[sIrfCompressor, '-9'],
									stdin = procCpio.stdout, stdout = fileIrfArchive
								) as procCompress:
									procFind.wait()
									procCpio.wait()
									procCompress.wait()
							else:
								procFind.wait()
								procCpio.wait()
						del fileCpioStdout
			finally:
				self.einfo('Cleaning up initramfs ...\n')
				os.chdir(sPrevDir)
				shutil.rmtree(sIrfWorkDir)

			self.eoutdent()


	def install(self):
		"""Installs the kernel image, modules and optional initramfs to their respective positions
		within the root directory specified in the constructor.
		"""

		self.build_dst_paths(self._m_sRoot)

		if self._m_sRoot == '/':
			self.einfo('Installing kernel\n')
		else:
			self.einfo('Installing kernel to {}\n'.format(self._m_sRoot))
		self.eindent()

		# Ensure /boot is mounted.
		bUnmountBoot = False
		sBootDir = os.path.join(self._m_sRoot, 'boot')
		if not os.path.isdir(sBootDir):
			os.mkdir(sBootDir, 0o755)
		# /boot should contain a symlink to itself (“.”) named “boot”.
		if not os.path.isdir(os.path.join(sBootDir, 'boot')):
			# Maybe /boot needs to be mounted. Can’t just run mount /boot, since sBootDir is not
			# necessarily “/”.
			listMountBootArgs = None
			with open(os.path.join(self._m_sRoot, 'etc/fstab'), 'r') as fileFsTab:
				for sLine in fileFsTab:
					# Look for a non-comment line for /boot.
					if re.match(r'^[^#]\S*\s+/boot\s', sLine):
						# Break up the line.
						listFields = re.split(r'\s+', sLine)
						listMountBootArgs = [
							'mount', listFields[0], '-t', listFields[2], '-o', listFields[3], sBootDir
						]
						del listFields
						break
			if listMountBootArgs:
				self.einfo('Mounting {} to {}\n'.format(listMountBootArgs[1], sBootDir))
				subprocess.check_call(listMountBootArgs, stdout = self._m_fileNullOut)
				bUnmountBoot = True
				del listMountBootArgs

		# Use a try/finally construct to ensure we do unmount /boot if we mounted it.
		try:
			cbKernelImage = 0
			cbModules = 0
			cbIrfArchive = 0
			# We’ll remove any initramfs-{self._m_sKernelVersion}.cpio.*, not just the one we’re
			# going to replace; this ensures we don’t leave around a leftover initramfs just because
			# it uses a different compression algorithm.
			listDstIrfArchivePaths = glob.glob(
				os.path.splitext(self._m_sDstIrfArchivePath)[0] + '.*'
			)
			if os.path.exists(self._m_sDstImagePath) or \
				os.path.exists(self._m_sDstConfigPath) or \
				os.path.exists(self._m_sDstSysmapPath) or \
				os.path.exists(self._m_sDstModulesDir) or \
				listDstIrfArchivePaths \
			:
				self.einfo('Removing old files ...\n')
				try:
					cbKernelImage = os.path.getsize(self._m_sDstImagePath)
					os.unlink(self._m_sDstImagePath)
				except OSError:
					pass
				try:
					os.unlink(self._m_sDstConfigPath)
				except OSError:
					pass
				try:
					os.unlink(self._m_sDstSysmapPath)
				except OSError:
					pass
				# Remove every in-tree kernel module, leaving only the out-of-tree ones.
				cbModules = self.modules_size(self._m_sDstModulesDir)

				eme = ExternalModuleEnumerator(bFirmware = False, bModules = True)
				sModuleFiles = '\n'.join(list(eme.files()))
				sModuleFiles = re.sub(r'^', '! -path */', sModuleFiles, flags = re.MULTILINE)

				subprocess.check_call(
					['find', self._m_sDstModulesDir] + shlex.split(sModuleFiles) +
						['(', '!', '-type', 'd', '-o', '-empty', ')', '-delete']
				)
				# Delete any initramfs archive.
				for s in listDstIrfArchivePaths:
					cbIrfArchive += os.path.getsize(s)
					os.unlink(s)
			del listDstIrfArchivePaths

			self.einfo('Installing kernel image ...\n')
			shutil.copy2(self._m_sSrcImagePath, self._m_sDstImagePath)
			shutil.copy2(self._m_sSrcConfigPath, self._m_sDstConfigPath)
			shutil.copy2(self._m_sSrcSysmapPath, self._m_sDstSysmapPath)
			if cbKernelImage:
				self.eindent()
				self.einfo_sizediff('Kernel', cbKernelImage, os.path.getsize(self._m_sDstImagePath))
				self.eoutdent()

			self.einfo('Installing modules ...')
			cModules = 0
			with subprocess.Popen(
				self._m_listKMakeArgs + ['INSTALL_MOD_PATH=' + self._m_sRoot, 'modules_install'],
				stdout = subprocess.PIPE, universal_newlines = True
			) as procKmake:
				for sLine in procKmake.stdout:
					if re.match(r'^\s*INSTALL\s+\S+\.ko$', sLine):
						cModules += 1
			sys.stdout.write(' ({})\n'.format(cModules))

			if cbModules:
				self.eindent()
				self.einfo_sizediff('Modules', cbModules, self.modules_size(self._m_sDstModulesDir))
				self.eoutdent()

			if self._m_sIrfSourcePath:
				self.einfo('Installing initramfs ...\n')
				shutil.copy2(self._m_sSrcIrfArchivePath, self._m_sDstIrfArchivePath)
				if cbIrfArchive:
					self.eindent()
					self.einfo_sizediff(
						'initramfs', cbIrfArchive, os.path.getsize(self._m_sDstIrfArchivePath)
					)
					self.eoutdent()
		finally:
			if bUnmountBoot:
				self.einfo('Unmounting {} ...\n'.format(sBootDir))
				subprocess.check_call(['umount', sBootDir], stdout = self._m_fileNullOut)

		self.eoutdent()


	def package(self, sPackageFileName):
		"""Generates a package (tarball) containing the same files that would be installed by
		install(): kernel image, modules, and optional initramfs.

		str sPackageFileName
			Full path of the package file that will be created.
		"""

		sPackageRoot = os.path.join(self._m_PTmpDir, 'pkg-' + self._m_sKernelVersion)
		shutil.rmtree(sPackageRoot, ignore_errors = True)
		os.makedirs(os.path.join(sPackageRoot, 'lib/modules'), 0o755, exist_ok = True)
		try:
			self.build_dst_paths(sPackageRoot)

			self.einfo('Preparing kernel package\n')
			self.eindent()

			self.einfo('Adding kernel image ...\n')
			shutil.copy2(self._m_sSrcImagePath, self._m_sDstImagePath)
			shutil.copy2(self._m_sSrcConfigPath, self._m_sDstConfigPath)
			shutil.copy2(self._m_sSrcSysmapPath, self._m_sDstSysmapPath)

			self.einfo('Adding modules ...\n')
			subprocess.check_call(
				self._m_listKMakeArgs + ['INSTALL_MOD_PATH=' + sPackageRoot, 'modules_install'],
				stdout = self._m_fileNullOut
			)

			if self._m_sIrfSourcePath:
				self.einfo('Adding initramfs ...\n')
				shutil.copy2(self._m_sSrcIrfArchivePath, self._m_sDstIrfArchivePath)

			self.einfo('Creating archive ...\n')
			subprocess.check_call(
				['tar', '-C', sPackageRoot, '-cjf', sPackageFileName, 'boot', 'lib'],
				stdout = self._m_fileNullOut
			)
		finally:
			self.einfo('Cleaning up kernel package ...\n')
			shutil.rmtree(sPackageRoot)

		self.eoutdent()

