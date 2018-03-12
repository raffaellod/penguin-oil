# -*- coding: utf-8; mode: python; tab-width: 3; indent-tabs-mode: nil -*-
#
# Copyright 2012-2018 Raffaello D. Di Napoli
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

"""Implementation of the class Generator."""

import glob
import os
import portage.package.ebuild.config as portage_config
import re
import shlex
import shutil
import subprocess
import sys
from . import OutOfTreeEnumerator


def makedirs(path):
   """Implementation of os.makedirs(exists_ok=True) for both Python 2.7 and
   3.x.

   str path
      Full path to the directory that should exist.
   """

   try:
      os.makedirs(path)
   except OSError:
      if not os.path.isdir(path):
         raise

##############################################################################
# Compressor

class Compressor(object):
   """Stores information about an external compressor program."""

   def __init__(self, config_name, ext, cmd_args):
      """Constructor.

      str config_name
         Name of the compressor as per Linux’s .config file.
      str ext
         Default file name extension for files compressed by this program.
      iterable(str*) cmd_args
         Command-line arguments to use to run the compressor.
      """

      self._cmd_args = cmd_args
      self._config_name = config_name
      self._ext = ext

   def cmd_args(self):
      """Returns the command-line arguments to use to run the compressor.

      iterable(str*) return
         Command-line arguments.
      """

      return self._cmd_args

   def enabled_in_config(self, kernel_config, prefix):
      """Checks if the compressor is enabled, with the given prefix, in the
      specified kernel configuration.

      dict(str: str) kernel_config
         Kernel configuration map.
      str prefix
         Configuration entry prefix.
      bool return
         True if the compressor is enabled, or False otherwise.
      """

      if self._config_name:
         return prefix + self._config_name in kernel_config
      else:
         # Nothing to check; it’s always enabled.
         return True

   def file_name_ext(self):
      """Returns the default file name extension for files compressed by this
      program.

      str return
         File name extension, including the dot.
      """

      return self._ext

##############################################################################
# GeneratorError

class GeneratorError(Exception):
   """Indicates a failure in the generation of a kernel binary package."""

   pass

##############################################################################
# Generator

class Generator(object):
   """Generates a Portage binary package containing a kernel image and related
   in-tree kernel modules, optionally generating an initramfs from a
   compatible self-contained initramfs-building system such as Tinytium.
   """

   # List of supported compressors, in order of preference.
   _compressors = [
      Compressor('LZO',   '.lzo' , ('lzop',  '-9')),
      Compressor('LZMA',  '.lzma', ('lzma',  '-9')),
      Compressor('BZIP2', '.bz2' , ('bzip2', '-9')),
      Compressor('GZIP',  '.gz'  , ('gzip',  '-9')),
      Compressor(None,    ''     , ('cat',       )),
   ]
   # ebuild template that will be dropped in the selected overlay and made
   # into a binary package.
   _ebuild_template = '''
      EAPI=5

      SLOT="${PVR}"
      DESCRIPTION="Linux kernel image and in-tree modules"
      HOMEPAGE="http://www.kernel.org"
      LICENSE="GPL-2"

      inherit mount-boot

      KEYWORDS="${ARCH}"
      # Avoid stripping kernel binaries.
      RESTRICT="strip"

      S="${WORKDIR}"

      src_install() {
         echo "KERNEL-GEN: D=${D}"
      }
   '''.replace('\n      ', '\n').rstrip(' ')
   # Special cases for the conversion from Portage ARCH to Linux ARCH.
   _portage_arch_to_kernel_arch = {
      'amd64': 'x86_64',
      'arm64': 'aarch64',
      'm68k' : 'm68',
      'ppc'  : 'powerpc',
      'ppc64': 'powerpc64',
      'x86'  : 'i386',
   }

   def __init__(self, root = None, portage_arch = None):
      """Constructor.

      str root
         Portage root directory; defaults to Portage’s ${ROOT}.
      str portage_arch
         Portage architecture; defaults to Portage’s ${ARCH}.
      """

      if root:
         # Set this now to override Portage’s default root.
         os.environ['ROOT'] = root
      self._category = None # Set by make_package_name()
      self._portage_config = portage_config.config()
      if not root:
         # Set this now to override the null root with Portage’s default root.
         os.environ['ROOT'] = root = self._portage_config['ROOT']
      self._cross_compiler_prefix = None
      self._dev_null = open(os.devnull, 'w')
      self._ebuild_file_path = None
      self._ebuild_pkg_root = None
      self._indent = ''
      self._irf_compressor = None
      self._irf_archive_path = None
      self._irf_source_path = None
      self._kernel_release = None # Set by set_sources()
      self._kernel_version = None # Set by set_sources()
      self._kmake_args = ['make']
      self._kmake_args.extend(shlex.split(self._portage_config['MAKEOPTS']))
      self._kmake_env = dict(os.environ)
      if not portage_arch:
         portage_arch = self._portage_config['ARCH']
      self._kmake_env['ARCH'] = self._portage_arch_to_kernel_arch.get(
         portage_arch, portage_arch
      )
      self._module_packages = None # Set by build_kernel()
      self._package_name = None # Set by make_package_name()
      self._package_version = None # Set by make_package_name()
      self._root = root
      self._source_path = None
      self._src_config_path = None
      self._src_image_path = None

   def __del__(self):
      """Destructor."""

      if self._ebuild_file_path:
         self.einfo('Cleaning up package build temporary directory')
         clean_proc = subprocess.Popen(
            ('ebuild', self._ebuild_file_path, 'clean'),
            stdout=self._dev_null, stderr=subprocess.STDOUT
         )
         clean_proc.communicate()
         clean_proc = None

         self.einfo('Deleting temporary ebuild')
         os.unlink(self._ebuild_file_path)
         # Delete the package directory, since now it should only contain the
         # Manifest file.
         package_path = os.path.dirname(self._ebuild_file_path)
         files_list = os.listdir(package_path)
         for i, file_path in enumerate(files_list):
            if file_path == 'Manifest':
               os.unlink(os.path.join(package_path, file_path))
               del files_list[i]
               break
         if files_list:
            ewarn(
               'Not removing {} unknown files in package directory `{}\''
               .format(len(files_list), package_path)
            )
         else:
            os.rmdir(package_path)

      self._dev_null.close()

   def build_initramfs(self, debug = False):
      """Builds an initramfs for the kernel generated by build_kernel().

      bool debug
         If True, the contents of the generated initramfs will be dumped to a
         file for later inspection.
      """

      self.einfo('Generating initramfs')
      self.eindent()

      prev_cwd = os.getcwd()
      irf_work_path = os.path.join(self._ebuild_pkg_root, 'initramfs-build')
      os.mkdir(irf_work_path)
      os.chdir(irf_work_path)

      self.einfo('Adding kernel modules')
      self.kmake_check_call(
         'INSTALL_MOD_PATH=' + irf_work_path, 'modules_install'
      )
      # TODO: configuration-driven exclusion of modules from the initramfs.
      excluded_mod_dirs = set([
         'arch/x86/kvm',
         'drivers/bluetooth',
         'drivers/media',
         'net/bluetooth',
         'net/netfilter',
         'sound',
         'vhost',
      ])
      # Equivalent to executing:
      #    rm -rf ${irf_work_path}/lib*/modules/*/kernel/${excluded_mod_dirs}
      for dir in os.listdir(irf_work_path):
         if dir.startswith('lib'):
            modules_dir = os.path.join(irf_work_path, dir, 'modules')
            for dir in os.listdir(modules_dir):
               kernel_modules_dir = os.path.join(modules_dir, dir, 'kernel')
               for dir in excluded_mod_dirs:
                  dir = os.path.join(kernel_modules_dir, dir)
                  # Recursively remove the excluded directory.
                  shutil.rmtree(dir, ignore_errors=True)

      self.einfo('Adding out-of-tree firmware')
      # Create the directory beforehand; it not needed, we'll delete it later.
      src_firmware_path = os.path.join(self._root, 'lib/firmware')
      dst_firmware_path = os.path.join(irf_work_path, 'lib/firmware')
      oote = OutOfTreeEnumerator(firmware=True, modules=False)
      for src_ext_firmware_path in oote.files():
         dst_ext_firmware_path = os.path.join(
            dst_firmware_path, src_ext_firmware_path
         )
         makedirs(os.path.dirname(dst_ext_firmware_path))
         # Copy the firmware file.
         shutil.copy2(
            os.path.join(src_firmware_path, src_ext_firmware_path),
            dst_ext_firmware_path
         )

      irf_build_path = os.path.join(self._irf_source_path, 'build')
      if os.path.isfile(irf_build_path) and \
         os.access(irf_build_path, os.R_OK | os.X_OK) \
      :
         # The initramfs has a build script; invoke it.
         self.einfo('Invoking initramfs custom build script')
         self.eindent()
         irf_build_env = dict(os.environ)
         irf_build_env['ARCH'] = self._kmake_env['ARCH']
         if self._cross_compiler_prefix:
            irf_build_env['CROSS_COMPILE'] = self._cross_compiler_prefix
         irf_build_env['PORTAGE_ARCH'] = self._portage_config['ARCH']
         try:
            subprocess.check_call((irf_build_path, ), env = irf_build_env)
         finally:
            self.eoutdent()
         del irf_build_env
      else:
         # No build script; just copy every file.
         self.einfo('Adding source files')
         for irf_file in os.listdir(self._irf_source_path):
            shutil.copytree(
               os.path.join(self._irf_source_path, irf_file), irf_work_path
            )

      cpio_input_bytes = self.list_initramfs_contents(irf_work_path, debug)
      self.create_initramfs_archive(cpio_input_bytes)

      # Get out of and remove the working directory, to avoid including it in
      # the binary package.
      os.chdir(prev_cwd)
      shutil.rmtree(irf_work_path)

      self.eoutdent()

   def build_kernel(self, rebuild_out_of_tree_modules = True):
      """Builds the kernel image and modules.

      bool rebuild_out_of_tree_modules
         If True, packages that provide out-of-tree modules will be rebuilt in
         order to ensure binary compatibility with the kernel being built.
      """

      self.einfo('Ready to build:')
      self.eindent()
      self.einfo('\033[1;32mlinux-{}\033[0m ({})'.format(
         self._kernel_release, self._kmake_env['ARCH']
      ))
      self.einfo('from \033[1;37m{}\033[0m'.format(self._source_path))

      if self._irf_source_path:
         # Check that a valid initramfs directory was specified.
         self._irf_source_path = os.path.realpath(self._irf_source_path)
         self.einfo('with initramfs from \033[1;37m{}\033[0m'.format(
            self._irf_source_path
         ))
      if self._cross_compiler_prefix:
         self.einfo(
            'cross-compiled with \033[1;37m{}\033[0m toolchain'
            .format(self._cross_compiler_prefix)
         )
      self.eoutdent()

      # Use distcc, if enabled.
      # TODO: also add HOSTCC.
      if 'distcc' in self._portage_config.features:
         self.einfo('Distributed C compiler (distcc) enabled')
         self._kmake_args.append('CC=distcc')
         distcc_dir = os.path.join(
            self._portage_config['PORTAGE_TMPDIR'], 'portage/.distcc'
         )
         old_umask = os.umask(0o002)
         makedirs(distcc_dir)
         os.umask(old_umask)
         self._kmake_env['DISTCC_DIR'] = distcc_dir

      # Only invoke make if .config was changed since last compilation.
      # Note that this check only works due to what we’ll do after invoking
      # kmake (see below, at the end of the if block), because kmake won’t
      # touch the kernel image if .config doesn’t require so, which means that
      # .config can be still more recent than the image even after kmake
      # completes, and this would cause this if branch to be always entered.
      if not os.path.exists(self._src_image_path) or \
         os.path.getmtime(self._src_config_path) > \
         os.path.getmtime(self._src_image_path) \
      :
         if rebuild_out_of_tree_modules:
            self.einfo('Preparing to rebuild out-of-tree kernel modules')
            self.kmake_check_call('modules_prepare')

            self.einfo('Getting a list of out-of-tree kernel modules')
            oote = OutOfTreeEnumerator(firmware=False, modules=True)
            self._module_packages = tuple(oote.packages())
            if self._module_packages:
               self.einfo('Rebuilding out-of-tree kernel modules\' packages')
               # First make sure that all the modules’ dependencies are
               # installed.
               self.emerge_check_call(None,
                  '--changed-use', '--onlydeps', '--update',
                  *self._module_packages
               )
               # Then (re)build the modules, but only generate their binary
               # packages.
               emerge_env = dict(os.environ)
               emerge_env['KERNEL_DIR'] = self._source_path
               self.emerge_check_call(emerge_env,
                  '--buildpkgonly', '--usepkg=n', *self._module_packages
               )
               del emerge_env

         self.einfo('Building kernel image and in-tree modules')
         self.kmake_check_call()

         # Touch the kernel image now, to avoid always re-running kmake (see
         # large comment above).
         os.utime(self._src_image_path, None)

   def create_ebuild(self, overlay_name = None):
      """Creates the temporary ebuild from which a binary package will be
      created later.

      str overlay_name
         Name of ther overlay in which the package ebuild will be added;
         defaults to the overlay with the highest priority.
      """

      # Get the specified overlay or the one with the highest priority.
      if overlay_name is None:
         overlay_name = self._portage_config.repositories.prepos_order[-1]
      povl = self._portage_config.repositories.prepos.get(overlay_name)
      if not povl:
         self.eerror('Unknown overlay: {}'.format(overlay_name))
         raise GeneratorError()
      self.einfo(
         'Creating temporary ebuild \033[1;32m{}/{}-{}::{}\033[0m'.format(
            self._category, self._package_name, self._package_version,
            overlay_name
         )
      )
      # Generate a new ebuild at the expected location in the selected
      # overlay.
      package_path = os.path.join(
         povl.location, self._category, self._package_name
      )
      makedirs(package_path)
      self._ebuild_file_path = os.path.join(
         package_path, '{}-{}.ebuild'.format(
            self._package_name, self._package_version
         )
      )
      with open(self._ebuild_file_path, 'wt') as ebuild_file:
         ebuild_file.write(self._ebuild_template)

      # Have Portage create the package installation image for the ebuild. The
      # ebuild will output the destination path, ${D}, using a pattern
      # specific to kernel-gen.
      out = subprocess.check_output(
         ('ebuild', self._ebuild_file_path, 'clean', 'manifest', 'install'),
         stderr=subprocess.STDOUT, universal_newlines=True
      )
      match = re.search(r'^KERNEL-GEN: D=(?P<D>.*)$', out, re.MULTILINE)
      self._ebuild_pkg_root = match.group('D')

   def create_initramfs_archive(self, cpio_input_bytes):
      """Creates a cpio archive containing the contents of the initramfs,
      named self._irf_archive_path.

      bytes cpio_input_bytes
         NUL-delimited list of file paths.
      """

      self.einfo('Creating archive')
      with open(self._irf_archive_path, 'wb') as irf_archive_file:
         # Spawn the compressor or just a cat.
         compress_proc = subprocess.Popen(
            self._irf_compressor.cmd_args(),
            stdin=subprocess.PIPE, stdout=irf_archive_file
         )
         # Make cpio write to the compressor’s input, and redirect its stderr
         # to /dev/null since it likes to output junk.
         cpio_proc = subprocess.Popen(
            ('cpio', '--create', '--format=newc', '--null', '--owner=0:0'),
            stdin=subprocess.PIPE,
            stdout=compress_proc.stdin, stderr=self._dev_null
         )
#         # Send cpio the list of files to package.
#         cpio_proc.communicate(cpio_input_bytes)
         # Use find . to enumerate the files for cpio to pack.
         find_proc = subprocess.Popen(
            ('find', '.', '-print0'), stdout=cpio_proc.stdin
         )

         find_proc.communicate()
         cpio_proc.communicate()
         compress_proc.communicate()

   def eerror(self, s):
      """TODO: comment"""

      print(self._indent + '[E] ' + s)

   def eindent(self):
      """TODO: comment"""

      self._indent += '  '

   def einfo(self, s):
      """TODO: comment"""

      print(self._indent + '[I] ' + s)

   def emerge_check_call(self, env, *args):
      """Invokes emerge in “quiet” mode with the specified additional command-
      line arguments.

      dict(str: str) env
         Environment variable dictionary to use in place of os.environ; if
         None, os.environ will be used.
      iterable(str*) args
         Additional arguments to pass to emerge.
      """

      all_args = [(self._cross_compiler_prefix or '') + 'emerge']
      verbose = False
      if verbose:
         all_args.append('--verbose')
      else:
         all_args.extend(('--quiet', '--quiet-build', '--quiet-fail=y'))
      all_args.extend(args)
      subprocess.check_call(
         all_args, env=env, stdout=(None if verbose else self._dev_null)
      )

   def eoutdent(self):
      """TODO: comment"""

      self._indent = self._indent[:-2]

   def ewarn(self, s):
      """TODO: comment"""

      print(self._indent + '[W] ' + s)

   def install(self, include_out_of_tree_modules = True):
      """Installs the generated kernel binary package.

      bool include_out_of_tree_modules
         If True, also install packages that provide out-of-tree modules.
      """

      self.einfo(
         'Installing kernel binary package \033[1;35m{}/{}-{}\033[0m'.format(
            self._category, self._package_name, self._package_version
         )
      )
      self.emerge_check_call(
         None, '--select', '--usepkgonly=y', '={}/{}-{}'.format(
            self._category, self._package_name, self._package_version
         )
      )
      if include_out_of_tree_modules:
         if self._module_packages is None:
            # build_kernel() hasn’t been called, so we need to scan for
            # out-of-tree modules now.
            oote = OutOfTreeEnumerator(firmware=False, modules=True)
            self._module_packages = tuple(oote.packages())
         if self._module_packages:
            self.einfo(
               'Installing out-of-tree kernel modules\' binary packages'
            )
            self.emerge_check_call(
               None, '--oneshot', '--usepkgonly=y', *self._module_packages
            )

   def kmake_call_kernelversion(self):
      """Retrieves the kernel version for the source directory specified in
      the constructor.

      str return
         Kernel version reported by “make kernelversion”.
      """

      # Ignore errors; if no source directory can be found, we’ll take care of
      # failing.
      make_proc = subprocess.Popen(
         self._kmake_args + [
            '--directory', self._source_path, '--quiet', 'kernelversion'
         ],
         env=self._kmake_env, stdout=subprocess.PIPE, stderr=self._dev_null,
         universal_newlines=True
      )
      ret = make_proc.communicate()[0].rstrip()
      # Expect a single line; if multiple lines are present, they must be
      # errors.
      if make_proc.returncode == 0 and '\n' not in ret:
         return ret
      else:
         return None

   def kmake_check_call(self, *args):
      """Invokes kmake with the specified additional command-line arguments.

      iterable(str*) args
         Additional arguments to pass to kmake.
      """

      all_args = list(self._kmake_args)
      all_args.append('--quiet')
      all_args.extend(args)
      subprocess.check_output(
         all_args, env=self._kmake_env
      )

   def kmake_check_output(self, target):
      """Runs kmake to build the specified informative target, such as
      “kernelrelease”.

      str target
         Target to “build”.
      str return
         Output of kmake.
      """

      all_args = list(self._kmake_args)
      all_args.append('--quiet')
      all_args.append(target)
      ret = subprocess.check_output(
         all_args, env=self._kmake_env,
         stderr=subprocess.STDOUT, universal_newlines=True
      ).rstrip()
      if '\n' in ret:
         self.eerror('Unexpected output by make {}:'.format(target))
         self.eerror(ret)
         raise GeneratorError()
      return ret

   def list_initramfs_contents(self, irf_work_path, debug):
      """Builds a list with every file path that cpio should package, relative
      to irf_work_path.

      str irf_work_path
         Temporary directory in which the initramfs image has been built; this
         is also the current directory.
      bool debug
         If True, the contents of the generated initramfs will be dumped to a
         file for later inspection.
      bytes return
         NUL-delimited list of file paths.
      """

      self.einfo('Collecting file names')
      irf_contents = []
      irf_work_path_len = len(irf_work_path) + 1
      for base_path, _, file_names in os.walk(irf_work_path):
         # Strip the work directory, changing irf_work_path into ‘.’.
         base_path = base_path[irf_work_path_len:]
         if base_path:
            base_path += '/'
         for file_name in file_names:
            irf_contents.append(base_path + file_name)
      if debug:
         irf_dump_file_path = os.path.join(
            os.environ.get('TMPDIR', '/tmp'),
            'initramfs-' + self._kernel_release + '.ls'
         )
         with open(irf_dump_file_path, 'w') as irf_dump_file:
            self.einfo('Dumping contents of generated initramfs to {}'.format(
               irf_dump_file_path
            ))
            subprocess.check_call(
               ['ls', '-lR', '--color=always'] + irf_contents,
               stdout=irf_dump_file, universal_newlines=True
            )
#      return b'\0'.join(
#         bytes(path, encoding='utf-8') for path in irf_contents
#      )

   def load_kernel_config(self):
      """Loads the selected kernel configuration file (.config), storing the
      entries defined in it and verifying that it’s for the correct kernel
      version.

      dict(str: str) return
         Loaded kernel configuration.
      """

      kernel_config = {}
      with open(self._src_config_path, 'r') as config_file:
         config_version_found = False
         for line_no, line in enumerate(config_file, start=1):
            line = line.rstrip()
            if not config_version_found:
               # In the first 5 lines, expect to find a line that indicates
               # the kernel has already been configured.
               if line_no == 5:
                  self.eerror(
                     'This kernel needs to be configured first; try:'
                  )
                  self.eerror('  make -C \'{}\' nconfig'.format(
                     self._source_path
                  ))
                  raise GeneratorError()

               match = re.match(
                  # Match: “Linux/i386 2.6.37 Kernel Configuration”.
                  r'^# Linux/\S* (?P<version>\S*) Kernel Configuration$', line
               ) or re.match(
                  # Match: “Linux kernel version: 2.6.34”.
                  r'^# Linux kernel version: (?P<version>\S+)', line
               )
               if match:
                  config_version_found = (
                     match.group('version') == self._kernel_version
                  )
            elif not line.startswith('#'):
               match = re.match(
                  r'^(?P<name>CONFIG_\S+)+=(?P<value>.*)$', line
               )
               if match:
                  value = match.group('value')
                  if value == 'y':
                     value = True
                  elif value in ('n', 'm'):
                     # Consider modules as missing, since checks for CONFIG_*
                     # values in this class would hardly consider modules as
                     # satisfying.
                     continue
                  elif len(value) >= 2 and \
                     value.startswith('"') and value.endswith('"') \
                  :
                     value = value[1:-1]
                  kernel_config[match.group('name')] = value
      return kernel_config

   def make_package_name(self, kernel_config):
      """Generates category, name and version for the binary package that will
      be generated.

      dict(str: str) kernel_config
         Kernel configuration.
      """

      self._category = 'sys-kernel'
      match = re.match(
         r'(?P<ver>(?:\d+\.)*\d+)-?(?P<extra>.*?)?(?P<rev>(?:-r|_p)\d+)?$',
         self._kernel_version
      )
      # Build the package name.
      self._package_name = match.group('extra') or 'vanilla'
      local_version = kernel_config.get('CONFIG_LOCALVERSION')
      if local_version:
         self._package_name += local_version
      self._package_name += '-bin'
      # Build the package name with version.
      self._package_version = match.group('ver') + (match.group('rev') or '')

   def package(self, irf_debug = False):
      """Generates a Portage binary package (.tbz2) containing the kernel
      image, in-tree modules, and optional initramfs.

      bool irf_debug
         If True, the contents of the generated initramfs will be dumped to a
         file for later inspection.
      """

      # Inject the package contents into ${D}.

      self.einfo('Adding kernel image')
      os.mkdir(os.path.join(self._ebuild_pkg_root, 'boot'))
      shutil.copy2(self._src_config_path, os.path.join(
         self._ebuild_pkg_root, 'boot/config-' + self._kernel_release
      ))
      shutil.copy2(
         os.path.join(self._source_path, 'System.map'),
         os.path.join(
            self._ebuild_pkg_root, 'boot/System.map-' + self._kernel_release
         )
      )
      shutil.copy2(self._src_image_path, os.path.join(
         self._ebuild_pkg_root, 'boot/linux-' + self._kernel_release
      ))
      # Create a symlink for compatibility with GRUB’s /etc/grub.d/10_linux
      # detection script.
      os.symlink('linux-' + self._kernel_release, os.path.join(
         self._ebuild_pkg_root, 'boot/kernel-' + self._kernel_release
      ))

      self.einfo('Adding modules')
      self.kmake_check_call(
         'INSTALL_MOD_PATH=' + self._ebuild_pkg_root, 'modules_install'
      )

      if self._irf_source_path:
         self._irf_archive_path = os.path.join(
            self._ebuild_pkg_root, 'boot/initramfs-{}.cpio{}'.format(
               self._kernel_release, self._irf_compressor.file_name_ext()
            )
         )
         self.build_initramfs(irf_debug)
         # Create a symlink for compatibility with GRUB’s /etc/grub.d/10_linux
         # detection script.
         os.symlink(
            os.path.basename(self._irf_archive_path),
            os.path.dirname(self._irf_archive_path) +
               '/initramfs-{}.img'.format(self._kernel_release)
         )

      # Complete the package creation, which will grab everything that’s in
      # ${D}.
      self.einfo('Creating package')
      subprocess.check_call(
         ('ebuild', self._ebuild_file_path, 'package'),
         stdout=self._dev_null, stderr=subprocess.STDOUT
      )

   def set_sources(self, source_path = None, irf_source_path = None):
      """Assigns a kernel source path, loading and validating the
      configuration found therein.

      str source_path
         Path to the kernel source, or None to default to /usr/src/linux.
      str irf_source_path
         Path to an initramfs source directory, or None to default to
         /usr/src/initramfs.
      """

      self.einfo('Gathering kernel information')
      self._source_path = source_path
      self._irf_source_path = irf_source_path

      # Ensure we have a valid kernel source directory, and get its version.
      if self._source_path:
         kernel_version = self.kmake_call_kernelversion()
         if not kernel_version:
            self.eerror(
               'The path `{}\' doesn\'t seem to be a kernel source directory.'
               .format(self._source_path)
            )
            raise GeneratorError()
      else:
         self._source_path = os.getcwd()
         kernel_version = self.kmake_call_kernelversion()
         if not kernel_version:
            # No kernel was found ${PWD}: checking if ony can be found at
            # /usr/src/linux.
            self._source_path = os.path.join(self._root, 'usr/src/linux')
            if not os.path.isdir(self._source_path):
               self.eerror(
                  'No suitable kernel source directory could be found; ' +
                  'please specify one using'
               )
               self.eerror(
                  'the --source option, or invoke kernel-gen from within a ' +
                  'kernel source'
               )
               self.eerror('directory.')
               self.eerror(
                  'Alternatively, you can enable the ' +
                  '\033[1;34msymlink\033[0m USE flag to keep an up-to-date'
               )
               self.eerror(
                  'symlink to your current kernel source directory in ' +
                  '\033[1;36m/usr/src/linux\033[0m.'
               )
               raise GeneratorError()
            kernel_version = self.kmake_call_kernelversion()
            if not kernel_version:
               self.eerror(
                  'Unable to determine the version of the selected kernel '+
                  'source.'
               )
               raise GeneratorError()
      # self._source_path is valid; make it permanently part of
      # self._kmake_args.
      self._kmake_args[1:1] = ['--directory', self._source_path]
      self._kernel_version = kernel_version

      self._source_path = os.path.abspath(self._source_path)
      self._src_config_path = os.path.join(self._source_path, '.config')

      # Verify that the kernel has been configured, and get its release string
      # (= version + local).
      kernel_config = self.load_kernel_config()
      self._kernel_release = self.kmake_check_output('kernelrelease')

      # Get a compressor to use for the kernel image from the config file.
      for compr in self._compressors:
         if compr.enabled_in_config(kernel_config, 'CONFIG_KERNEL_'):
            kernel_compressor = compr
            break

      # Determine the location of the generated kernel image.
      image_path = self.kmake_check_output('image_name')
      self._src_image_path = os.path.join(self._source_path, image_path)
      del image_path

      if self._irf_source_path:
         if self._irf_source_path is True:
            if 'CONFIG_BLK_DEV_INITRD' not in kernel_config:
               self.ewarn(
                  'The selected kernel was not configured to support an ' +
                  'initramfs/initrd.'
               )
               self._irf_source_path = False
            else:
               self._irf_source_path = os.path.join(
                  self._root, 'usr/src/initramfs'
               )
               if not os.path.isdir(self._irf_source_path):
                  self.ewarn(
                     'The selected kernel was configured to support ' +
                     'initramfs/initrd, but no suitable'
                  )
                  self.ewarn(
                     'initramfs source directory was specified or found.'
                  )
                  self.ewarn('No initramfs will be created.')
                  self._irf_source_path = False
         else:
            if 'CONFIG_BLK_DEV_INITRD' not in kernel_config:
               self.eerror(
                  'The selected kernel was not configured to support an ' +
                  'initramfs/initrd.'
               )
               raise GeneratorError()
            if not os.path.isdir(self._irf_source_path):
               self.eerror(
                  'The initramfs path `{}\' is not a directory.'.format(
                     self._irf_source_path
                  )
               )
               raise GeneratorError()

      if self._irf_source_path:
         # TODO: check that these CONFIG_ match:
         #   +DEVTMPFS

         # Check for an enabled initramfs compression method.
         enabled_irf_compressors = []
         for compr in self._compressors:
            if compr.enabled_in_config(kernel_config, 'CONFIG_RD_'):
               if compr is kernel_compressor:
                  # We can pick the same compression for kernel image and
                  # initramfs.
                  self._irf_compressor = kernel_compressor
                  break
               # Not the same as the kernel image, but make a note of this in
               # case the condition above is never satisfied.
               enabled_irf_compressors.append(compr)
         else:
            # Pick the first enabled compression method.
            self._irf_compressor = enabled_irf_compressors[0]

      # Determine if cross-compiling.
      self._cross_compiler_prefix = kernel_config.get('CONFIG_CROSS_COMPILE')

      self.make_package_name(kernel_config)
