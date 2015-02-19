#!/usr/bin/python
# -*- coding: utf-8; mode: python; tab-width: 3; indent-tabs-mode: nil -*-
#
# Copyright 2012, 2013, 2014, 2015
# Raffaello D. Di Napoli
#
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
import portage.package.ebuild.config as portage_config
import re
import shlex
import shutil
import subprocess
import sys
from . import ExternalModuleEnumerator

####################################################################################################
# Compressor

class Compressor(object):
   """Stores information about an external compressor program."""

   def __init__(self, sConfigName, sExt, iterCmdArgs):
      """Constructor.

      str sConfig
         Name of the compressor as per Linux’s .config file.
      str sExt
         Default file name extension for files compressed by this program.
      iterable(str*) iterCmdArgs
         Command-line arguments to use to run the compressor.
      """

      self._m_iterCmdArgs = iterCmdArgs
      self._m_sConfigName = sConfigName
      self._m_sExt = sExt

   def cmd_args(self):
      """Returns the command-line arguments to use to run the compressor.

      iterable(str*) return
         Command-line arguments.
      """

      return self._m_iterCmdArgs

   def config_name(self):
      """Returns the name of the compressor as per Linux’s .config file.

      str return
         Compressor name.
      """

      return self._m_sConfigName

   def file_name_ext(self):
      """Returns the default file name extension for files compressed by this program.

      str return
         File name extension, including the dot.
      """

      return self._m_sExt

####################################################################################################
# Generator

class Generator(object):
   """Generates a kernel binary+modules and/or tarball, optionally generating an initramfs from a
   compatible self-contained initramfs-building system (such as tinytium).
   """

   # List of supported compressors, in order of preference.
   _smc_listCompressors = [
      Compressor('LZO',   '.lzo' , ('lzop',  '-9')),
      Compressor('LZMA',  '.lzma', ('lzma',  '-9')),
      Compressor('BZIP2', '.bz2' , ('bzip2', '-9')),
      Compressor('GZIP',  '.gz'  , ('gzip',  '-9')),
   ]
   _smc_sEbuildTemplate = re.sub(r'^ *', '', """
      EAPI=5

      LICENSE="GPL-2"
      SLOT="${PVR}"
      KEYWORDS="${ARCH}"
      DESCRIPTION="Linux kernel image and modules"
      HOMEPAGE="http://www.kernel.org"

      # TODO: does mount-boot really allow installing files in /boot correctly?
      inherit mount-boot

      S="${WORKDIR}"

      # Avoid stripping kernel binaries.
      RESTRICT="strip"

      src_install() {
         echo "KERNEL-GEN: D=${D}"
      }
   """, 0, re.MULTILINE)

   def __init__(self, sPArch, sIrfSourcePath, sRoot, sSourcePath):
      """Constructor. TODO: comment"""

      self._m_pconfig = portage_config.config()
      self._m_sCrossCompiler = None
      self._m_fileNullOut = open(os.devnull, 'w')
      self._m_sIndent = ''
      self._m_comprIrf = None
      self._m_sIrfSourcePath = sIrfSourcePath
      self._m_sKArch = None
      self._m_listKMakeArgs = ['make']
      self._m_listKMakeArgs.extend(shlex.split(self._m_pconfig['MAKEOPTS']))
      if sPArch is None:
         self._m_sPArch = self._m_pconfig['ARCH']
      else:
         self._m_sPArch = sPArch
      self._m_sPRoot = self._m_pconfig['EROOT']
      if sRoot is None:
         self._m_sRoot = self._m_sPRoot
      else:
         self._m_sRoot = sRoot
      self._m_sSourcePath = sSourcePath
      self._m_sSrcConfigPath = None
      self._m_sSrcImagePath = None
      self._m_sSrcIrfArchiveFile = None
      self._m_sSrcSysmapPath = None
      self._m_sTmpDir = self._m_pconfig['PORTAGE_TMPDIR']

   def __del__(self):
      """Destructor."""

      if self._m_sSrcIrfArchiveFile:
         self.einfo('Cleaning up temporary files\n')
         try:
            os.unlink(self._m_sSrcIrfArchiveFile)
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

   def einfo_sizediff(self, sObject, cbOld, cbNew):
      """Displays an einfo with a report on the size change (if any) of a (possibly not previously
      existing) file or folder.

      str sObject
         Description of the object that was measured.
      int cbOld
         Size of the previous version of the object, in bytes.
      int cbNew
         Size of the new version of the object, in bytes.
      """

      if cbOld == cbNew:
         self.einfo('{} size unchanged at {} KiB\n'.format(sObject, int((cbNew + 1023) / 1024)))
      elif cbOld == 0:
         self.einfo('{} size is {} KiB\n'.format(sObject, int((cbNew + 1023) / 1024)))
      else:
         self.einfo('{} size changed from {} KiB to {} KiB ({:+}%)\n'.format(
            sObject,
            int((cbOld + 1023) / 1024),
            int((cbNew + 1023) / 1024),
            int((cbNew - cbOld) * 100 / cbOld)
         ))

   def get_kernel_version(self):
      """Retrieves the kernel version for the source directory specified in the constructor.

      str return
         Kernel version reported by “make kernelversion”.
      """

      # Ignore errors; if no source directory can be found, we’ll take care of failing.
      with subprocess.Popen(
         self._m_listKMakeArgs + ['-C', self._m_sSourcePath, '-s', 'kernelversion'],
         stdout = subprocess.PIPE, stderr = self._m_fileNullOut, universal_newlines = True
      ) as procMake:
         sStdOut = procMake.communicate()[0].rstrip()
         # Expect a single line; if multiple lines are present, they must be errors.
         if procMake.returncode == 0 and '\n' not in sStdOut:
            return sStdOut
      return None

   def build_dst_paths(self, sRoot):
      """Calculates the destination paths for each file to be installed/packaged.

      str sRoot
         Absolute path to which the calculated paths will be relative.
      """

      self._m_sDstImagePath = os.path.join(sRoot, 'boot/linux-' + self._m_sKernelRelease)
      self._m_sDstIrfArchiveFile = os.path.join(sRoot, 'boot/initramfs-{}.cpio'.format(
         self._m_sKernelRelease
      ))
      if self._m_comprIrf:
         self._m_sDstIrfArchiveFile += self._m_comprIrf.file_name_ext()
      self._m_sDstConfigPath = os.path.join(sRoot, 'boot/config-'     + self._m_sKernelRelease)
      self._m_sDstSysmapPath = os.path.join(sRoot, 'boot/System.map-' + self._m_sKernelRelease)
      self._m_sDstModulesDir = os.path.join(sRoot, 'lib/modules/'     + self._m_sKernelRelease)

   @staticmethod
   def modules_size(sDir):
      """Calculates the size of the kernel modules contained in the specified directory.

      str sDir
         Directory containing kernel modules.
      int return
         Total size of the kernel modules in sDir, in bytes.
      """

      cbModules = 0
      for sBaseDir, _, listFileNames in os.walk(sDir):
         for sFileName in listFileNames:
            if sFileName.endswith('.ko'):
               cbModules += os.path.getsize(os.path.join(sBaseDir, sFileName))
      return cbModules

   def with_initramfs(self):
      """Returns True if an initramfs can and should be built for the kernel.

      bool return
         True if build_initramfs() should be called, or False otherwise.
      """

      return bool(self._m_sIrfSourcePath)

   def load_kernel_config(self, sConfigPath):
      """Loads the specified kernel configuration file (.config), storing the entries defined in it
      and verifying that it’s for the correct kernel version.

      str sConfigPath
         Path to the configuration file.
      """

      dictKernelConfig = {}
      with open(sConfigPath, 'r') as fileConfig:
         bConfigVersionFound = False
         for iLine, sLine in enumerate(fileConfig, start = 1):
            sLine = sLine.rstrip()
            if not bConfigVersionFound:
               # In the first 5 lines, expect to find a line that indicates the kernel has already
               # been configured.
               if iLine < 5:
                  # Match: “Linux/i386 2.6.37 Kernel Configuration”.
                  match = re.match(r'^# Linux/\S* (?P<version>\S*) Kernel Configuration$', sLine)
                  if not match:
                     # Match: “Linux kernel version: 2.6.34”.
                     match = re.match(r'^# Linux kernel version: (?P<version>\S+)', sLine)
                  if match:
                     bConfigVersionFound = match.group('version') == self._m_sKernelVersion
                     continue
               else:
                  self.eerror('This kernel needs to be configured first.\n')
                  self.eerror('Try:\n')
                  self.eerror("  make -C '{}' menuconfig\n".format(self._m_sSourcePath))
            else:
               match = re.match(r'^(?P<name>CONFIG_\S+)+=(?P<value>.*)$', sLine)
               if match:
                  sValue = match.group('value')
                  if sValue == 'y':
                     oValue = True
                  elif sValue == 'n' or sValue == 'm':
                     # Consider modules as missing, since checks for CONFIG_* values in this class
                     # would hardly consider modules as satisfying.
                     continue
                  elif len(sValue) >= 2 and sValue[0] == '"' and sValue[-1] == '"':
                     oValue = sValue[1:-1]
                  else:
                     oValue = sValue
                  dictKernelConfig[match.group('name')] = oValue
      self._m_dictKernelConfig = dictKernelConfig

   def prepare(self):
      """Prepares for the execution of the build_kernel() and build_initramfs() methods."""

      self.einfo('Preparing to build kernel\n')

      # Determine the Linux ARCH from Portage’s ARCH, considering these special cases.
      dictPArchToKArch = {
         'amd64': 'x86_64',
         'arm64': 'aarch64',
         'm68k' : 'm68',
         'ppc'  : 'powerpc',
         'ppc64': 'powerpc64',
         'x86'  : 'i386',
      }
      self._m_sKArch = dictPArchToKArch.get(self._m_sPArch, self._m_sPArch)
      os.environ['ARCH'] = self._m_sKArch
      os.environ['PORTAGE_ARCH'] = self._m_sPArch

      # Ensure we have a valid kernel, and get its version.
      sKernelVersion = self.get_kernel_version()
      if not sKernelVersion:
         # No kernel was specified: find one, first checking if the standard symlink is in place.
         self._m_sSourcePath = os.path.join(self._m_sPRoot, 'usr/src/linux')
         if not os.path.isdir(self._m_sSourcePath):
            self.eerror(
               'No suitable kernel source directory was found; please consider using the\n'
            )
            self.eerror(
               '--source option, or invoke kernel-gen from within a kernel source directory.\n'
            )
            self.eerror('\n')
            self.eerror(
               'You can enable the \033[1;34msymlink\033[0m USE flag to keep an up-to-date ' +
               'symlink to your\n'
            )
            self.eerror('current kernel source directory in \033[1;36m/usr/src/linux\033[0m.\n')
            self.eerror('\n')
            self.eerror('Unable to locate a kernel source directory.\n')
         sKernelVersion = self.get_kernel_version()
         if not sKernelVersion:
            raise Exception('unable to determine the version of the selected kernel source')
      # self._m_sSourcePath is valid; make it permanently part of self._m_listKMakeArgs.
      self._m_listKMakeArgs[1:1] = ['-C', self._m_sSourcePath]
      self._m_sKernelVersion = sKernelVersion

      self._m_sSourcePath = os.path.abspath(self._m_sSourcePath)
      self._m_sSrcConfigPath = os.path.join(self._m_sSourcePath, '.config')
      self._m_sSrcSysmapPath = os.path.join(self._m_sSourcePath, 'System.map')
      os.environ['KERNEL_DIR'] = self._m_sSourcePath
      os.environ['ROOT'] = self._m_sRoot

      # Verify that the kernel has been configured, and get its release string (= version + local).
      self.load_kernel_config(self._m_sSrcConfigPath)
      self._m_sKernelRelease = self.kmake_get('kernelrelease')

      # Get compressor to use for the kernel image from the config file.
      for compr in self._smc_listCompressors:
         if ('CONFIG_KERNEL_' + compr.config_name()) in self._m_dictKernelConfig:
            comprKernel = compr
            break
      else:
         comprKernel = None

      # Determine the location of the generated kernel image.
      sImagePath = self.kmake_get('image_name')
      self._m_sSrcImagePath = os.path.join(self._m_sSourcePath, sImagePath)
      del sImagePath

      if self._m_sIrfSourcePath:
         # Check for initramfs/initrd support with the config file.
         if 'CONFIG_BLK_DEV_INITRD' not in self._m_dictKernelConfig:
            raise Exception('the selected kernel was not configured to support initramfs/initrd')
         if self._m_sIrfSourcePath is True:
            self._m_sIrfSourcePath = os.path.join(self._m_sPRoot, 'usr/src/initramfs')
         if not os.path.isdir(self._m_sIrfSourcePath):
            self.ewarn('The selected kernel was configured to support initramfs/initrd,\n')
            self.ewarn('but no suitable initramfs source directory was specified or found.\n')
            self.ewarn('No initramfs will be created.\n')
            self._m_sIrfSourcePath = False

      if self.with_initramfs():
         # TODO: check that these CONFIG_ match:
         #   +DEVTMPFS

         # Check for an enabled initramfs compression method.
         listEnabledIrfCompressors = []
         for compr in self._smc_listCompressors:
            if ('CONFIG_RD_' + compr.config_name()) in self._m_dictKernelConfig:
               if compr is comprKernel:
                  # We can pick the same compression for kernel image and initramfs.
                  self._m_comprIrf = comprKernel
                  break
               # Not the same as the kernel image, but make a note of this in case the condition
               # above is never satisfied.
               listEnabledIrfCompressors.append(compr)
         # If this is still None, pick the first enabled compression method, if any.
         if not self._m_comprIrf and listEnabledIrfCompressors:
            self._m_comprIrf = listEnabledIrfCompressors[0]
         self._m_sSrcIrfArchiveFile = os.path.join(self._m_sTmpDir, 'initramfs.cpio')
         if self._m_comprIrf:
            self._m_sSrcIrfArchiveFile += self._m_comprIrf.file_name_ext()

      # Determine if cross-compiling.
      self._m_sCrossCompiler = self._m_dictKernelConfig.get('CONFIG_CROSS_COMPILE')
      if self._m_sCrossCompiler:
         os.environ['CROSS_COMPILE'] = self._m_sCrossCompiler

   def build_kernel(self, bRebuildOutOfTreeModules = True):
      """Builds the kernel image and modules.

      bool bRebuildOutOfTreeModules
         If True, packages that install out-of-tree modules will be rebuilt in order to ensure
         binary compatibility with the kernel being built.
      """

      self.einfo('Ready to build:\n')
      self.eindent()
      self.einfo('\033[1;32mlinux-{}\033[0m ({})\n'.format(self._m_sKernelRelease, self._m_sKArch))
      self.einfo('from \033[1;37m{}\033[0m\n'.format(self._m_sSourcePath))

      if self._m_sIrfSourcePath:
         # Check that a valid initramfs directory was specified.
         self._m_sIrfSourcePath = os.path.realpath(self._m_sIrfSourcePath)
         self.einfo('with initramfs from \033[1;37m{}\033[0m\n'.format(self._m_sIrfSourcePath))
      if self._m_sCrossCompiler:
         self.einfo('cross-compiled with \033[1;37m{}\033[0m toolchain\n'.format(
            self._m_sCrossCompiler
         ))
      self.eoutdent()

      # Use distcc, if enabled.
      # TODO: also add HOSTCC.
      if 'distcc' in self._m_pconfig.features:
         self.einfo('Distributed C compiler (distcc) enabled\n')
         self._m_listKMakeArgs.append('CC=distcc')
         sDistCCDir = os.path.join(self._m_sTmpDir, 'portage/.distcc')
         iOldMask = os.umask(0o002)
         os.makedirs(sDistCCDir, exist_ok = True)
         os.umask(iOldMask)
         os.environ['DISTCC_DIR'] = sDistCCDir

      # Only invoke make if .config was changed since last compilation.
      # Note that this check only works due to what we’ll do after invoking kmake (see below, at the
      # end of the if block), because kmake won’t touch the kernel image if .config doesn’t require
      # so, which means that .config can be still more recent than the image even after kmake
      # completes, and this would cause this if branch to be always entered.
      if not os.path.exists(self._m_sSrcImagePath) or \
         os.path.getmtime(self._m_sSrcConfigPath) > os.path.getmtime(self._m_sSrcImagePath) \
      :
         if bRebuildOutOfTreeModules:
            self.einfo('Preparing to rebuild out-of-tree kernel modules\n')
            subprocess.check_call(
               self._m_listKMakeArgs + ['modules_prepare'], stdout = self._m_fileNullOut
            )
            self.einfo('Finished building linux-{}\n'.format(self._m_sKernelRelease))

            self.einfo('Rebuilding out-of-tree kernel modules\n')
            eme = ExternalModuleEnumerator(bFirmware = False, bModules = True)
            listModulePackages = list(eme.packages())
            if listModulePackages:
               subprocess.check_call([
                  self._m_sCrossCompiler + 'emerge',
                  '--oneshot', '--quiet', '--quiet-build', '--usepkg=n'
               ] + listModulePackages, stdout = self._m_fileNullOut)

         self.einfo('Building kernel image and in-tree modules\n')
         subprocess.check_call(self._m_listKMakeArgs, stdout = self._m_fileNullOut)

         # Touch the kernel image now, to avoid always re-running kmake (see large comment above).
         os.utime(self._m_sSrcImagePath, None)

   def build_initramfs(self, bDebug = False):
      """Builds an initramfs for the kernel generated by build_kernel().

      bool bDebug
         If True, the contents of the generated initramfs will be dumped to a file for later
         inspection.
      """

      if not self._m_sIrfSourcePath:
         raise Exception('no initramfs source path specified')

      self.einfo('Generating initramfs\n')
      self.eindent()

      sPrevDir = os.getcwd()
      sIrfWorkDir = os.path.join(self._m_sTmpDir, 'initramfs-' + self._m_sKernelRelease)
      shutil.rmtree(sIrfWorkDir, ignore_errors = True)
      os.mkdir(sIrfWorkDir)
      try:
         os.chdir(sIrfWorkDir)

         self.einfo('Adding kernel modules\n')
         subprocess.check_call(
            self._m_listKMakeArgs + ['INSTALL_MOD_PATH=' + sIrfWorkDir, 'modules_install'],
            stdout = self._m_fileNullOut
         )
         # TODO: configuration-driven exclusion of modules from the initramfs.
         setExcludedModDirs = set([
            'arch/x86/kvm',
            'drivers/bluetooth',
            'drivers/media',
            'net/bluetooth',
            'net/netfilter',
            'sound',
            'vhost',
         ])
         # Equivalent to executing:
         #    rm -rf sIrfWorkDir/lib*/modules/*/kernel/{${setExcludedModDirs}}
         for sDir in os.listdir(sIrfWorkDir):
            if sDir.startswith('lib'):
               sModulesDir = os.path.join(sIrfWorkDir, sDir, 'modules')
               for sDir in os.listdir(sModulesDir):
                  sKernelModulesDir = os.path.join(sModulesDir, sDir, 'kernel')
                  for sDir in setExcludedModDirs:
                     sDir = os.path.join(sKernelModulesDir, sDir)
                     # Recursively remove the excluded directory.
                     shutil.rmtree(sDir, ignore_errors = True)

         self.einfo('Adding out-of-tree firmware\n')
         # Create the folder beforehand; it not needed, we'll delete it later.
         sSrcFirmwareDir = os.path.join(self._m_sRoot, 'lib/firmware')
         sDstFirmwareDir = os.path.join(sIrfWorkDir, 'lib/firmware')
         eme = ExternalModuleEnumerator(bFirmware = True, bModules = False)
         for sSrcExtFirmwarePath in eme.files():
            sDstExtFirmwarePath = os.path.join(sDstFirmwareDir, sSrcExtFirmwarePath)
            os.makedirs(os.path.dirname(sDstExtFirmwarePath), exist_ok = True)
            # Copy the firmware file.
            shutil.copy2(os.path.join(sSrcFirmwareDir, sSrcExtFirmwarePath), sDstExtFirmwarePath)

         sIrfBuild = os.path.join(self._m_sIrfSourcePath, 'build')
         if os.path.isfile(sIrfBuild) and os.access(sIrfBuild, os.R_OK | os.X_OK):
            # The initramfs has a build script; invoke it.
            self.einfo('Invoking initramfs custom build script\n')
            self.eindent()
            # ARCH, PORTAGE_ARCH and CROSS_COMPILE are already set in os.environ.
            subprocess.check_call((sIrfBuild, ))
            self.eoutdent()
         else:
            # No build script; just copy every file.
            self.einfo('Adding source files\n')
            for sIrfFile in os.listdir(self._m_sIrfSourcePath):
               shutil.copytree(os.path.join(self._m_sIrfSourcePath, sIrfFile), sIrfWorkDir)

         # Build a list with every file name for cpio to package, relative to the current directory
         # (sIrfWorkDir).
         self.einfo('Collecting file names\n')
         listIrfContents = []
         for sBaseDir, _, listFileNames in os.walk(sIrfWorkDir):
            # Strip the work directory, changing sIrfWorkDir into ‘.’.
            sBaseDir = sBaseDir[len(sIrfWorkDir) + 1:]
            if sBaseDir:
               sBaseDir += os.sep
            for sFileName in listFileNames:
               listIrfContents.append(sBaseDir + sFileName)
         if bDebug:
            sIrfDumpFileName = os.path.join(
               self._m_sTmpDir, 'initramfs-' + self._m_sKernelRelease + '.ls'
            )
            with open(sIrfDumpFileName, 'w') as fileIrfDump:
               self.einfo('Dumping contents of generated initramfs to {}\n'.format(
                  sIrfDumpFileName
               ))
               subprocess.check_call(
                  ['ls', '-lR', '--color=always'] + listIrfContents,
                  stdout = fileIrfDump, universal_newlines = True
               )
#         byCpioInput = b'\0'.join(bytes(sPath, encoding = 'utf-8') for sPath in listIrfContents)
         del listIrfContents

         self.einfo('Creating archive\n')
         with open(self._m_sSrcIrfArchiveFile, 'wb') as fileIrfArchive:
            # Spawn the compressor or just a cat.
            if self._m_comprIrf:
               tplCompressorArgs = self._m_comprIrf.cmd_args()
            else:
               tplCompressorArgs = ('cat', )
            with subprocess.Popen(
               tplCompressorArgs, stdin = subprocess.PIPE, stdout = fileIrfArchive
            ) as procCompress:
               # Make cpio write to the compressor’s input, and redirect its stderr to /dev/null
               # since it likes to output junk.
               with subprocess.Popen(
                  ('cpio', '--create', '--format=newc', '--owner=0:0', '-0'),
                  stdin = subprocess.PIPE, stdout = procCompress.stdin, stderr = self._m_fileNullOut
               ) as procCpio:
#                  # Send cpio the list of files to package.
#                  procCpio.communicate(byCpioInput)
                  # Use find . to enumerate the files for cpio to pack.
                  with subprocess.Popen(
                     ('find', '.', '-print0'), stdout = procCpio.stdin
                  ) as procFind:
                     procFind.communicate()
                  procCpio.communicate()
               procCompress.communicate()
      finally:
         self.einfo('Cleaning up initramfs\n')
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
         os.mkdir(sBootDir)
      # /boot should contain a symlink to itself (“.”) named “boot”.
      if not os.path.isdir(os.path.join(sBootDir, 'boot')):
         # Maybe /boot needs to be mounted. Can’t just run mount /boot, since sBootDir is not
         # necessarily “/”.
         with open(os.path.join(self._m_sRoot, 'etc/fstab'), 'r') as fileFsTab:
            for sLine in fileFsTab:
               # Look for a non-comment line for /boot.
               if re.match(r'^[^#]\S*\s+/boot\s', sLine):
                  # Break up the line.
                  listFields = re.split(r'\s+', sLine)
                  self.einfo('Mounting {} to {}\n'.format(listFields[0], sBootDir))
                  subprocess.check_call((
                     'mount', listFields[0], '-t', listFields[2], '-o', listFields[3], sBootDir
                  ), stdout = self._m_fileNullOut)
                  bUnmountBoot = True
                  break

      # Use a try/finally construct to ensure we do unmount /boot if we mounted it.
      try:
         cbKernelImage = 0
         cbModules = 0
         cbIrfArchive = 0
         # We’ll remove any initramfs-${self._m_sKernelRelease}.cpio.*, not just the one we’re going
         # to replace; this ensures we don’t leave around a leftover initramfs just because it uses
         # a different compression algorithm.
         sDstIrfArchiveFileNoExt = os.path.splitext(self._m_sDstIrfArchiveFile)[0]
         tplDstIrfArchiveFiles = tuple(filter(os.path.exists, [
            sDstIrfArchiveFileNoExt + compr.file_name_ext() for compr in self._smc_listCompressors
         ]))
         setAccessoryFilesSubst = set([
            (self._m_sSrcImagePath,  self._m_sDstImagePath),
            (self._m_sSrcConfigPath, self._m_sDstConfigPath),
            (self._m_sSrcSysmapPath, self._m_sDstSysmapPath),
         ])
         if \
            any(os.path.exists(sDstFilePath) for _, sDstFilePath in setAccessoryFilesSubst) or \
            os.path.exists(self._m_sDstModulesDir) or tplDstIrfArchiveFiles \
         :
            self.einfo('Removing old files\n')

            # Delete an old image.
            try:
               cbKernelImage = os.path.getsize(self._m_sDstImagePath)
            except OSError:
               pass
            for _, sDstFilePath in setAccessoryFilesSubst:
               try:
                  os.unlink(sDstFilePath)
               except OSError:
                  pass

            # Remove every in-tree kernel module, leaving only the out-of-tree ones.
            cbModules = self.modules_size(self._m_sDstModulesDir)
            listArgs = ['find', self._m_sDstModulesDir]
            eme = ExternalModuleEnumerator(bFirmware = False, bModules = True)
            for sModuleFile in eme.files():
               listArgs.extend(('!', '-path', '*/' + sModuleFile))
            listArgs.extend(('(', '!', '-type', 'd', '-o', '-empty', ')', '-delete'))
            subprocess.check_call(listArgs)

            # Delete any initramfs archive.
            for s in tplDstIrfArchiveFiles:
               cbIrfArchive += os.path.getsize(s)
               os.unlink(s)

         self.einfo('Installing kernel image\n')
         for sSrcFilePath, sDstFilePath in setAccessoryFilesSubst:
            shutil.copy2(sSrcFilePath, sDstFilePath)
         if cbKernelImage:
            self.eindent()
            self.einfo_sizediff('Kernel', cbKernelImage, os.path.getsize(self._m_sDstImagePath))
            self.eoutdent()

         self.einfo('Installing modules')
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

         if self.with_initramfs():
            self.einfo('Installing initramfs\n')
            shutil.copy2(self._m_sSrcIrfArchiveFile, self._m_sDstIrfArchiveFile)
            if cbIrfArchive:
               self.eindent()
               self.einfo_sizediff(
                  'initramfs', cbIrfArchive, os.path.getsize(self._m_sDstIrfArchiveFile)
               )
               self.eoutdent()
      finally:
         if bUnmountBoot:
            self.einfo('Unmounting {}\n'.format(sBootDir))
            subprocess.check_call(('umount', sBootDir), stdout = self._m_fileNullOut)

      self.eoutdent()

   def kmake_get(self, sTarget):
      """Runs kmake to build the specified informative target, such as “kernelrelease”.

      str sTarget
         Target to “build”.
      str return
         Output of kmake.
      """

      sOut = subprocess.check_output(
         self._m_listKMakeArgs + ['-s', sTarget],
         stderr = subprocess.STDOUT, universal_newlines = True
      )
      sOut = sOut.rstrip()
      if '\n' in sOut:
         self.eerror('unexpected output by make {}:\n{}'.format(sTarget, sOut))
      return sOut

   def package(self, sOverlayName = None):
      """Generates a Portage binary package (.tbz2) containing the files that would be installed by
      install(): kernel image, modules, and optional initramfs.

      str sOverlayName
         Name of ther overlay in which the package ebuild will be added; defaults to the overlay
         with the highest priority.
      """

      sCategory = 'sys-kernel'
      match = re.match(
         r'(?P<ver>(?:\d+\.)*\d+)-?(?P<extra>.*?)?(?P<rev>(?:-r|_p)\d+)?$', self._m_sKernelVersion
      )
      # Build the package name.
      if match.group('extra'):
         sPackageName = match.group('extra')
      else:
         sPackageName = 'vanilla'
      sLocalVersion = self._m_dictKernelConfig.get('CONFIG_LOCALVERSION')
      if sLocalVersion:
         sPackageName += sLocalVersion
      sPackageName += '-bin'
      # Build the package name with version.
      sPackageNameVersion = sPackageName + '-' + match.group('ver')
      if match.group('rev'):
         sPackageNameVersion += match.group('rev')
      # Get the specified overlay or the one with the highest priority.
      if sOverlayName is None:
         sOverlayName = self._m_pconfig.repositories.prepos_order[-1]
      povl = self._m_pconfig.repositories.prepos.get(sOverlayName)
      if not povl:
         self.eerror('Unknown overlay: {}\n'.format(sOverlayName))

      self.einfo('Creating binary package \033[1;35m{}/{}::{}\033[0m\n'.format(
         sCategory, sPackageNameVersion, sOverlayName
      ))
      self.eindent()

      # Generate a new ebuild at the expected location in the selected overlay.
      sEbuildFilePath = os.path.join(povl.location, sCategory, sPackageName)
      os.makedirs(sEbuildFilePath, exist_ok = True)
      sEbuildFilePath = os.path.join(sEbuildFilePath, sPackageNameVersion + '.ebuild')
      with open(sEbuildFilePath, 'wt') as fileEbuild:
         fileEbuild.write(self._smc_sEbuildTemplate)

      dictEbuildEnv = dict(os.environ)
      try:
         # Have Portage create the package installation image for the ebuild. The ebuild will output
         # the destination path, ${D}, using a pattern specific to kernel-gen.
         sOut = subprocess.check_output(
            ('ebuild', sEbuildFilePath, 'clean', 'manifest', 'install'),
            env = dictEbuildEnv, stderr = subprocess.STDOUT, universal_newlines = True
         )
         match = re.search(r'^KERNEL-GEN: D=(?P<D>.*)$', sOut, re.MULTILINE)
         sPackageRoot = match.group('D')

         # Inject the package contents into ${D}.
         self.build_dst_paths(sPackageRoot)
         self.einfo('Adding kernel image\n')
         os.mkdir(os.path.join(sPackageRoot, 'boot'))
         shutil.copy2(self._m_sSrcImagePath,  self._m_sDstImagePath)
         shutil.copy2(self._m_sSrcConfigPath, self._m_sDstConfigPath)
         shutil.copy2(self._m_sSrcSysmapPath, self._m_sDstSysmapPath)
         self.einfo('Adding modules\n')
         subprocess.check_call(
            self._m_listKMakeArgs + ['INSTALL_MOD_PATH=' + sPackageRoot, 'modules_install'],
            stdout = self._m_fileNullOut
         )
         if self.with_initramfs():
            self.einfo('Adding initramfs\n')
            shutil.copy2(self._m_sSrcIrfArchiveFile, self._m_sDstIrfArchiveFile)

         # Complete the package creation, which will grab everything that’s in ${D}.
         self.einfo('Creating archive\n')
         subprocess.check_call(
            ('ebuild', sEbuildFilePath, 'package'),
            env = dictEbuildEnv, stdout = self._m_fileNullOut, stderr = subprocess.STDOUT
         )
      finally:
         self.eoutdent()
         self.einfo('Cleaning up package build temporary directory\n')
         with subprocess.Popen(
            ('ebuild', sEbuildFilePath, 'clean'),
            env = dictEbuildEnv, stdout = self._m_fileNullOut, stderr = subprocess.STDOUT
         ) as procClean:
            procClean.communicate()
