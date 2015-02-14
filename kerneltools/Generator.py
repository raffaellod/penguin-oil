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
import portage
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

   def __init__(self, sPArch, sIrfSourceDir, bIrfDebug, bRebuildModules, sRoot, sSourceDir):
      """Constructor. TODO: comment"""

      self._m_sCrossCompiler = None
      self._m_fileNullOut = open(os.devnull, 'w')
      self._m_sIndent = ''
      self._m_comprIrf = None
      self._m_bIrfDebug = bIrfDebug
      self._m_sIrfSourceDir = sIrfSourceDir
      self._m_sKArch = None
      self._m_listKMakeArgs = ['make']
      self._m_listKMakeArgs.extend(shlex.split(portage.settings['MAKEOPTS']))
      if sPArch is None:
         self._m_sPArch = portage.settings['ARCH']
      else:
         self._m_sPArch = sPArch
      self._m_sPRoot = portage.settings['EROOT']
      self._m_bRebuildModules = bRebuildModules
      if sRoot is None:
         self._m_sRoot = self._m_sPRoot
      else:
         self._m_sRoot = sRoot
      self._m_sSourceDir = sSourceDir
      self._m_sSrcConfigFile = None
      self._m_sSrcImageFile = None
      self._m_sSrcIrfArchiveFile = None
      self._m_sSrcSysmapPath = None
      self._m_sTmpDir = portage.settings['PORTAGE_TMPDIR']

   def __del__(self):
      """Destructor."""

      if self._m_sSrcIrfArchiveFile:
         self.einfo('Cleaning up temporary files ...\n')
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
         Kernel version reported by “make kernelrelease”. Also available as self._m_sKernelVersion.
      """

      # Ignore errors; if no source directory can be found, we’ll take care of failing.
      with subprocess.Popen(
         self._m_listKMakeArgs + ['-C', self._m_sSourceDir, '-s', 'kernelrelease'],
         stdout = subprocess.PIPE, stderr = self._m_fileNullOut, universal_newlines = True
      ) as procMake:
         sStdOut = procMake.communicate()[0].rstrip()
         # Expect a single line; if multiple lines are present, they must be errors.
         if procMake.returncode == 0 and '\n' not in sStdOut:
            # Store the kernel version and make the source dir permanently part of
            # self._m_listKMakeArgs.
            self._m_sKernelVersion = sStdOut
            self._m_listKMakeArgs[1:1] = ['-C', self._m_sSourceDir]
         else:
            self._m_sKernelVersion = None
      return self._m_sKernelVersion

   def build_dst_paths(self, sRoot):
      """Calculates the destination paths for each file to be installed/packaged.

      str sRoot
         Absolute path to which the calculated paths will be relative.
      """

      self._m_sDstImageFile = os.path.join(sRoot, 'boot/linux-' + self._m_sKernelVersion)
      self._m_sDstIrfArchiveFile = os.path.join(sRoot, 'boot/initramfs-{}.cpio'.format(
         self._m_sKernelVersion
      ))
      if self._m_comprIrf:
         self._m_sDstIrfArchiveFile += self._m_comprIrf.file_name_ext()
      self._m_sDstConfigFile = os.path.join(sRoot, 'boot/config-' + self._m_sKernelVersion)
      self._m_sDstSysmapFile = os.path.join(sRoot, 'boot/System.map-' + self._m_sKernelVersion)
      self._m_sDstModulesDir = os.path.join(sRoot, 'lib/modules/' + self._m_sKernelVersion)

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

   def should_build_initramfs(self):
      """Returns True if an initramfs can and should be built for the kernel.

      bool return
         True if build_initramfs() should be called, or False otherwise.
      """

      return bool(self._m_sIrfSourceDir)

   def load_kernel_config(self, sConfigFile, sKernelVersion):
      """Loads the specified kernel configuration file (.config), returning the entries defined in
      it and verifying that it’s for a specific kernel version.

      str sConfigFile
         Configuration file.
      str sKernelVersion
         Kernel version to be matched.
      dict(object) return
         Configuration entries.
      """

      dictKernelConfig = {}
      with open(sConfigFile, 'r') as fileConfig:
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
                     bConfigVersionFound = match.group('version') == sKernelVersion
                     continue
               else:
                  self.eerror('This kernel needs to be configured first.\n')
                  self.eerror('Try:\n')
                  self.eerror("  make -C '{}' menuconfig\n".format(self._m_sSourceDir))
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

   def prepare(self):
      """Prepares for the execution of the build_kernel() and build_initramfs() methods."""

      self.einfo('Preparing to build kernel ...\n')

      # Determine the ARCH and the generated kernel file name.
      self._m_sKArch = self._m_sPArch
      sSrcImageRelPath = None
      if self._m_sPArch == 'x86':
         self._m_sKArch = 'i386'
         sSrcImageRelPath = 'arch/x86/boot/bzImage'
      elif self._m_sPArch == 'amd64':
         self._m_sKArch = 'x86_64'
         sSrcImageRelPath = 'arch/x86/boot/bzImage'
      elif self._m_sPArch == 'ppc':
         self._m_sKArch = 'powerpc'
      else:
         raise Exception('unsupported ARCH: {}'.format(self._m_sPArch))
      os.environ['ARCH'] = self._m_sKArch
      os.environ['PORTAGE_ARCH'] = self._m_sPArch

      # Ensure we have a valid kernel, and get its version.
      if not self.get_kernel_version():
         # No kernel was specified: find one, first checking if the standard symlink is in place.
         self._m_sSourceDir = os.path.join(self._m_sPRoot, 'usr/src/linux')
         if not os.path.isdir(self._m_sSourceDir):
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
            raise Exception('unable to determine the version of the selected kernel source')

      self._m_sSourceDir = os.path.abspath(self._m_sSourceDir)
      self._m_sSrcConfigFile = os.path.join(self._m_sSourceDir, '.config')
      self._m_sSrcSysmapPath = os.path.join(self._m_sSourceDir, 'System.map')
      os.environ['KERNEL_DIR'] = self._m_sSourceDir
      os.environ['ROOT'] = self._m_sRoot

      dictKernelConfig = self.load_kernel_config(self._m_sSrcConfigFile, self._m_sKernelVersion)

      # Get compressor to use for the kernel image from the config file.
      for compr in self._smc_listCompressors:
         if dictKernelConfig.get('CONFIG_KERNEL_' + compr.config_name()):
            comprKernel = compr
            break
      else:
         comprKernel = None

      # If default, or if not compressed, just use the plain image.
      if not sSrcImageRelPath or not comprKernel:
         sSrcImageRelPath = 'vmlinux'
      self._m_sSrcImageFile = os.path.join(self._m_sSourceDir, sSrcImageRelPath)

      if self._m_sIrfSourceDir:
         # Check for initramfs/initrd support with the config file.
         if not dictKernelConfig.get('CONFIG_BLK_DEV_INITRD'):
            raise Exception('the selected kernel was not configured to support initramfs/initrd')
         if self._m_sIrfSourceDir is True:
            self._m_sIrfSourceDir = os.path.join(self._m_sPRoot, 'usr/src/initramfs')
         if not os.path.isdir(self._m_sIrfSourceDir):
            self.ewarn('The selected kernel was configured to support initramfs/initrd,\n')
            self.ewarn('but no suitable initramfs source directory was specified or found.\n')
            self.ewarn('No initramfs will be created.\n')
            self._m_sIrfSourceDir = None

      if self._m_sIrfSourceDir:
         # TODO: check that these CONFIG_ match:
         #   +DEVTMPFS

         # Check for an enabled initramfs compression method.
         listEnabledIrfCompressors = []
         for compr in self._smc_listCompressors:
            if dictKernelConfig.get('CONFIG_RD_' + compr.config_name()):
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
      self._m_sCrossCompiler = dictKernelConfig.get('CONFIG_CROSS_COMPILE')
      os.environ['CROSS_COMPILE'] = self._m_sCrossCompiler

   def build_kernel(self):
      """Builds the kernel image and modules."""

      self.einfo('Ready to build:\n')
      self.eindent()
      self.einfo('[1;32mlinux-{}[0m ({})\n'.format(self._m_sKernelVersion, self._m_sKArch))
      self.einfo('from [1;37m{}[0m\n'.format(self._m_sSourceDir))

      if self._m_sIrfSourceDir:
         # Check that a valid initramfs directory was specified.
         self._m_sIrfSourceDir = os.path.realpath(self._m_sIrfSourceDir)
         self.einfo('with initramfs from [1;37m{}[0m\n'.format(self._m_sIrfSourceDir))
      if self._m_sCrossCompiler:
         self.einfo('cross-compiled with [1;37m{}[0m toolchain\n'.format(
            self._m_sCrossCompiler
         ))
      self.eoutdent()

      # Use distcc, if enabled.
      # TODO: also add HOSTCC.
      if re.search(r'\bdistcc\b', portage.settings['FEATURES']):
         self.einfo('Distributed C compiler (distcc) enabled\n')
         self._m_listKMakeArgs.append('CC=distcc')
         sDistCCDir = os.path.join(self._m_sTmpDir, 'portage/.distcc')
         iOldMask = os.umask(0o002)
         os.makedirs(sDistCCDir, 0o775, exist_ok = True)
         os.umask(iOldMask)
         os.environ['DISTCC_DIR'] = sDistCCDir

      # Only invoke make if .config was changed since last compilation.
      # Note that this check only works due to what we’ll do after invoking kmake (see below),
      # because kmake won’t touch the kernel image if .config doesn’t require so, which means that
      # .config can be still more recent than the image even after kmake completes, and this would
      # cause this if branch to be always entered.
      if not os.path.exists(self._m_sSrcImageFile) or \
         os.path.getmtime(self._m_sSrcConfigFile) > os.path.getmtime(self._m_sSrcImageFile) \
      :
         self.einfo('Building linux-{} ...\n'.format(self._m_sKernelVersion))
         # TODO: support passing custom parameters to kmake.
         subprocess.check_call(self._m_listKMakeArgs, stdout = self._m_fileNullOut)
         self.einfo('Finished building linux-{}\n'.format(self._m_sKernelVersion))

         # Touch the kernel image now, to avoid always re-running kmake (see large comment above).
         os.utime(self._m_sSrcImageFile, None)

         if self._m_bRebuildModules:
            self.einfo('Rebuilding kernel module packages ...\n')
            eme = ExternalModuleEnumerator(bFirmware = False, bModules = True)
            listModulePackages = list(eme.packages())
            if listModulePackages:
               subprocess.check_call(
                  [self._m_sCrossCompiler + 'emerge', '-q1', '--usepkg=n', '--quiet-build'] +
                     listModulePackages,
                  stdout = self._m_fileNullOut
               )

   def build_initramfs(self):
      """Builds an initramfs for the kernel generated by build_kernel()."""

      if not self._m_sIrfSourceDir:
         raise Exception('no initramfs source path specified')

      self.einfo('Generating initramfs\n')
      self.eindent()

      sPrevDir = os.getcwd()
      sIrfWorkDir = os.path.join(self._m_sTmpDir, 'initramfs-' + self._m_sKernelVersion)
      shutil.rmtree(sIrfWorkDir, ignore_errors = True)
      os.mkdir(sIrfWorkDir, 0o755)
      try:
         os.chdir(sIrfWorkDir)

         self.einfo('Adding kernel modules ...\n')
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

         self.einfo('Adding out-of-tree firmware ...\n')
         # Create the folder beforehand; it not needed, we'll delete it later.
         sSrcFirmwareDir = os.path.join(self._m_sRoot, 'lib/firmware')
         sDstFirmwareDir = os.path.join(sIrfWorkDir, 'lib/firmware')
         eme = ExternalModuleEnumerator(bFirmware = True, bModules = False)
         for sSrcExtFirmwarePath in eme.files():
            sDstExtFirmwarePath = os.path.join(sDstFirmwareDir, sSrcExtFirmwarePath)
            os.makedirs(os.path.dirname(sDstExtFirmwarePath), 0o755, exist_ok = True)
            # Copy the firmware file.
            shutil.copy2(os.path.join(sSrcFirmwareDir, sSrcExtFirmwarePath), sDstExtFirmwarePath)

         sIrfBuild = os.path.join(self._m_sIrfSourceDir, 'build')
         if os.path.isfile(sIrfBuild) and os.access(sIrfBuild, os.R_OK | os.X_OK):
            # The initramfs has a build script; invoke it.
            self.einfo('Invoking initramfs custom build script\n')
            self.eindent()
            # ARCH, PORTAGE_ARCH and CROSS_COMPILE are already set in os.environ.
            subprocess.check_call((sIrfBuild, ))
            self.eoutdent()
         else:
            # No build script; just copy every file.
            self.einfo('Adding source files ...\n')
            for sIrfFile in os.listdir(self._m_sIrfSourceDir):
               shutil.copytree(os.path.join(self._m_sIrfSourceDir, sIrfFile), sIrfWorkDir)

         # Build a list with every file name for cpio to package, relative to the current directory
         # (sIrfWorkDir).
         self.einfo('Collecting file names ...\n')
         listIrfContents = []
         for sBaseDir, _, listFileNames in os.walk(sIrfWorkDir):
            # Strip the work directory, changing sIrfWorkDir into ‘.’.
            sBaseDir = sBaseDir[len(sIrfWorkDir) + 1:]
            if sBaseDir:
               sBaseDir += os.sep
            for sFileName in listFileNames:
               listIrfContents.append(sBaseDir + sFileName)
         if self._m_bIrfDebug:
            sIrfDumpFileName = os.path.join(
               self._m_sTmpDir, 'initramfs-' + self._m_sKernelVersion + '.ls'
            )
            with open(sIrfDumpFileName, 'w') as fileIrfDump:
               self.einfo('Dumping contents of generated initramfs to {} ...\n'.format(
                  sIrfDumpFileName
               ))
               subprocess.check_call(
                  ['ls', '-lR', '--color=always'] + listIrfContents,
                  stdout = fileIrfDump, universal_newlines = True
               )
#         byCpioInput = b'\0'.join(bytes(sPath, encoding = 'utf-8') for sPath in listIrfContents)
         del listIrfContents

         self.einfo('Creating archive ...\n')
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
         # We’ll remove any initramfs-${self._m_sKernelVersion}.cpio.*, not just the one we’re going
         # to replace; this ensures we don’t leave around a leftover initramfs just because it uses
         # a different compression algorithm.
         sDstIrfArchiveFileNoExt = os.path.splitext(self._m_sDstIrfArchiveFile)[0]
         tplDstIrfArchiveFiles = tuple(filter(os.path.exists, [
            sDstIrfArchiveFileNoExt + compr.file_name_ext() for compr in self._smc_listCompressors
         ]))
         setAccessoryFilesSubst = set([
            (self._m_sSrcImageFile, self._m_sDstImageFile),
            (self._m_sSrcConfigFile, self._m_sDstConfigFile),
            (self._m_sSrcSysmapPath, self._m_sDstSysmapFile),
         ])
         if \
            any(os.path.exists(sDstFilePath) for _, sDstFilePath in setAccessoryFilesSubst) or \
            os.path.exists(self._m_sDstModulesDir) or tplDstIrfArchiveFiles \
         :
            self.einfo('Removing old files ...\n')
            try:
               cbKernelImage = os.path.getsize(self._m_sDstImageFile)
            except OSError:
               pass
            for _, sDstFilePath in setAccessoryFilesSubst:
               try:
                  os.unlink(sDstFilePath)
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
            for s in tplDstIrfArchiveFiles:
               cbIrfArchive += os.path.getsize(s)
               os.unlink(s)

         self.einfo('Installing kernel image ...\n')
         for sSrcFilePath, sDstFilePath in setAccessoryFilesSubst:
            shutil.copy2(sSrcFilePath, sDstFilePath)
         if cbKernelImage:
            self.eindent()
            self.einfo_sizediff('Kernel', cbKernelImage, os.path.getsize(self._m_sDstImageFile))
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

         if self._m_sIrfSourceDir:
            self.einfo('Installing initramfs ...\n')
            shutil.copy2(self._m_sSrcIrfArchiveFile, self._m_sDstIrfArchiveFile)
            if cbIrfArchive:
               self.eindent()
               self.einfo_sizediff(
                  'initramfs', cbIrfArchive, os.path.getsize(self._m_sDstIrfArchiveFile)
               )
               self.eoutdent()
      finally:
         if bUnmountBoot:
            self.einfo('Unmounting {} ...\n'.format(sBootDir))
            subprocess.check_call(('umount', sBootDir), stdout = self._m_fileNullOut)

      self.eoutdent()

   def package(self, sPackageFile):
      """Generates a package (tarball) containing the same files that would be installed by
      install(): kernel image, modules, and optional initramfs.

      str sPackageFile
         Full path of the package file that will be created.
      """

      sRepoPath = os.path.join(self._m_sTmpDir, 'repo-' + self._m_sKernelVersion)
      # TODO: don’t hard-code “vanilla”.
      sEbuildFilePath = os.path.join(sRepoPath, 'sys-kernel', 'vanilla-bin')
      shutil.rmtree(sRepoPath, ignore_errors = True)
      os.makedirs(sEbuildFilePath, 0o755, exist_ok = True)
      # TODO: get the kernelversion string and change it from x.y.z-string to string-bin-x.y.z .
      sEbuildFilePath = os.path.join(
         sEbuildFilePath, 'vanilla-bin-' + self._m_sKernelVersion + '.ebuild'
      )
      try:
         self.einfo('Creating package ...\n')
         self.eindent()
         dictEbuildEnv = dict(os.environ)
         dictEbuildEnv['PKGDIR'] = sRepoPath
         shutil.copy2('template.ebuild', sEbuildFilePath)

         # Have Portage create the package installation image for the ebuild. The ebuild will output
         # the destination path, ${D}, using a pattern specific to kernel-gen.
         sOut = subprocess.check_output(
            ('ebuild', sEbuildFilePath, 'clean', 'manifest', 'install'),
            env = dictEbuildEnv, universal_newlines = True, stderr = subprocess.STDOUT
         )
         match = re.search(r'^KERNEL-GEN: D=(?P<D>.*)$', sOut, re.MULTILINE)
         sPackageRoot = match.group('D')

         # Inject the package contents into ${D}.
         self.build_dst_paths(sPackageRoot)
         self.einfo('Adding kernel image ...\n')
         os.makedirs(os.path.join(sPackageRoot, 'boot'), 0o755, exist_ok = True)
         shutil.copy2(self._m_sSrcImageFile, self._m_sDstImageFile)
         shutil.copy2(self._m_sSrcConfigFile, self._m_sDstConfigFile)
         shutil.copy2(self._m_sSrcSysmapPath, self._m_sDstSysmapFile)
         self.einfo('Adding modules ...\n')
         subprocess.check_call(
            self._m_listKMakeArgs + ['INSTALL_MOD_PATH=' + sPackageRoot, 'modules_install'],
            stdout = self._m_fileNullOut
         )
         if self._m_sIrfSourceDir:
            self.einfo('Adding initramfs ...\n')
            shutil.copy2(self._m_sSrcIrfArchiveFile, self._m_sDstIrfArchiveFile)

         # Complete the package creation, which will grab everything that’s in ${D}.
         self.einfo('Creating archive ...\n')
         subprocess.check_call(
            ('ebuild', sEbuildFilePath, 'package'),
            env = dictEbuildEnv, stdout = self._m_fileNullOut, stderr = subprocess.STDOUT
         )
      finally:
         pass
#         self.einfo('Cleaning up kernel package ...\n')
#         shutil.rmtree(sRepoPath)

      self.eoutdent()
