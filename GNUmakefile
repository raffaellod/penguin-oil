# -*- coding: utf-8; mode: Makefile; tab-width: 3 -*-
#
# Copyright 2024 Raffaello D. Di Napoli <rafdev@dinapo.li>
# Distributed under the terms of the GNU General Public License v3

.POSIX:
.SUFFIXES:

# Configuration ---------------------------------------------------------------

INSTALL?=install
DEFAULT_INSTALL_ROOT:=/usr/local
INSTALL_ROOT?=$(DEFAULT_INSTALL_ROOT)
# Ensure $(INSTALL_ROOT) ends with a backslash.
override INSTALL_ROOT:=$(INSTALL_ROOT:%/=%)/

# Project-specific rules ------------------------------------------------------

.PHONY: help
help:
	@echo 'Example invocations:'
	@echo
	@echo 'make INSTALL_ROOT=$(DEFAULT_INSTALL_ROOT) install'
	@echo '  Install scripts into INSTALL_ROOT, which defaults to'\
	      '$(DEFAULT_INSTALL_ROOT).'

ALL_SCRIPTS:=\
   bin/kernel-config-required \
   bin/kernel-gen \
   bin/kernel-lsoot

.PHONY: install
define script_install_rule
install: $$(INSTALL_ROOT)usr/$(script_file)
$$(INSTALL_ROOT)usr/$(script_file): $(script_file)
	$$(INSTALL) -T $$< $$@
endef
$(foreach script_file,$(ALL_SCRIPTS),$(eval $(call script_install_rule)))
